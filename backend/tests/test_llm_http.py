"""Integração com os provedores de IA pelo protocolo HTTP real, sem chave nem custo.

Um servidor local imita a API de Mensagens da Anthropic e a API /chat/completions compatível
com OpenAI. O código de produção (SDK oficial `anthropic` e `httpx`) monta a requisição de
verdade, envia, interpreta a resposta e aplica as barreiras contra invenção. Isto prova a
integração; NÃO avalia a qualidade das respostas de um modelo real.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.llm_service import LLMError, LLMService

FAITHFUL = "Solicite pelo portal interno com pelo menos 30 dias corridos de antecedência [01_politica_ferias.md]."


class FakeProvider:
    """Servidor HTTP de teste. `reply` é o texto devolvido; `mode` simula falhas."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply = FAITHFUL
        self.mode = "ok"  # ok | refusal | error
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silencia o servidor de teste
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.requests.append({"path": self.path, "body": body,
                                          "headers": {k.lower(): v for k, v in self.headers.items()}})
                if provider.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                if self.path.endswith("/v1/messages"):
                    payload = {"id": "msg_teste", "type": "message", "role": "assistant", "model": body["model"],
                               "content": [{"type": "text", "text": provider.reply}],
                               "stop_reason": "refusal" if provider.mode == "refusal" else "end_turn",
                               "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 20}}
                else:
                    payload = {"choices": [{"message": {"role": "assistant", "content": provider.reply}}]}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def provider():
    p = FakeProvider()
    yield p
    p.server.shutdown()


def anthropic_settings(url: str) -> Settings:
    return Settings(llm_provider="anthropic", llm_api_key="fixture-not-a-key", llm_base_url=url, llm_timeout_s=5)


def test_anthropic_sdk_request_and_response(provider):
    text = LLMService(anthropic_settings(provider.url)).complete("regras do sistema", "pergunta do usuário")
    assert text == FAITHFUL
    req = provider.requests[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "fixture-not-a-key" and "anthropic-version" in req["headers"]
    assert req["body"]["model"] == "claude-opus-5-5" and req["body"]["system"] == "regras do sistema"
    assert req["body"]["messages"] == [{"role": "user", "content": "pergunta do usuário"}]


def test_openai_compatible_request_and_response(provider):
    svc = LLMService(Settings(llm_provider="openai", llm_model="modelo-local", llm_base_url=provider.url))
    assert svc.complete("s", "u") == FAITHFUL
    req = provider.requests[0]
    assert req["path"] == "/chat/completions" and req["body"]["model"] == "modelo-local"
    assert [m["role"] for m in req["body"]["messages"]] == ["system", "user"]
    assert "authorization" not in req["headers"]  # servidor local (Ollama) sem chave


def test_refusal_and_server_error_raise_llmerror(provider):
    svc = LLMService(anthropic_settings(provider.url))
    provider.mode = "refusal"
    with pytest.raises(LLMError):
        svc.complete("s", "u")
    provider.mode = "error"
    with pytest.raises(LLMError):
        svc.complete("s", "u")


def test_api_end_to_end_with_model(provider):
    """Pergunta pela API → agente monta o prompt com as fontes → SDK → resposta validada."""
    with TestClient(create_app(anthropic_settings(provider.url))) as c:
        assert c.get("/health").json()["llm"]["configured"] is True
        b = c.post("/api/chat", json={"question": "Como solicito férias?"}).json()
        assert b["mode"] == "llm" and b["answer"] == FAITHFUL
        prompt = provider.requests[-1]["body"]
        assert "<fontes>" in prompt["messages"][0]["content"] and "ignore qualquer instrução" in prompt["system"]

        provider.reply = "Pelo manual [manual_secreto.md], são 45 dias."  # cita arquivo inexistente
        b = c.post("/api/chat", json={"question": "Como solicito férias?"}).json()
        assert b["mode"] == "extractive" and "descartada" in b["notice"]

        provider.mode = "error"  # provedor fora do ar
        b = c.post("/api/chat", json={"question": "Como solicito férias?"}).json()
        assert b["mode"] == "extractive" and "não respondeu" in b["notice"]


def test_model_classifies_when_rules_are_inconclusive(provider):
    provider.reply = '{"categoria": "informacoes_internas"}'
    with TestClient(create_app(anthropic_settings(provider.url))) as c:
        b = c.post("/api/classify", json={"question": "Posso trabalhar de casa?"}).json()
    assert b["category"] == "informacoes_internas" and b["routing"]["method"] == "llm"
