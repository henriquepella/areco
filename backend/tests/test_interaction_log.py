"""Registro de interações (arquivos em tmp_path são fixtures de teste)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.interaction_log import InteractionLog, mask_personal_data


@pytest.mark.parametrize("raw,expected", [
    ("meu e-mail é fulano.silva@empresa.com.br", "meu e-mail é [email]"),
    ("CPF 123.456.789-09 e 12345678909", "CPF [cpf] e [cpf]"),
    ("ligue (11) 98765-4321", "ligue [telefone]"),
    ("cartão 4111111111111111", "cartão [numero]"),
    ("minha senha: hunter2", "minha senha: [segredo]"),
    ("chave sk-ant-abcdefgh12345678", "chave [segredo]"),
    ("Qual o status do protocolo OUV-0002?", "Qual o status do protocolo OUV-0002?"),
    ("despesas de janeiro a março de 2025", "despesas de janeiro a março de 2025"),
])
def test_mask_personal_data(raw, expected):
    assert mask_personal_data(raw) == expected


def _client(tmp_path):
    return TestClient(create_app(Settings(llm_provider="none", interaction_log_path=tmp_path / "i.jsonl")),
                      raise_server_exceptions=False)


def test_chat_writes_one_jsonl_line_per_interaction(tmp_path):
    with _client(tmp_path) as c:
        c.post("/api/chat", json={"question": "Qual o status do protocolo OUV-0002? meu cpf 123.456.789-09"})
        c.post("/api/chat", json={"question": "Qual a previsão do tempo?"})
        c.post("/api/chat", json={"question": ""})  # 422: não chega ao agente, não é registrada
    lines = (tmp_path / "i.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(l) for l in lines)
    assert first["category"] == "ouvidoria" and first["agent"] == "Agente de Ouvidoria"
    assert "123.456" not in first["question"] and "[cpf]" in first["question"]
    assert first["sources"][0]["document"] == "manifestacoes.csv"
    assert first["latency_ms"] >= 0 and first["routing_method"] == "rules"
    assert second["status"] == "out_of_scope" and second["agent"] is None


def test_answer_keeps_official_channels(tmp_path):
    with _client(tmp_path) as c:
        c.post("/api/chat", json={"question": "Como reporto um e-mail de phishing?"})
    entry = json.loads((tmp_path / "i.jsonl").read_text(encoding="utf-8"))
    assert "seguranca@aurora.example" in entry["answer"]


def test_errors_are_logged_without_details(tmp_path, monkeypatch):
    with _client(tmp_path) as c:
        def boom(_q):
            raise RuntimeError("detalhe interno")
        monkeypatch.setattr(c.app.state.orchestrator, "handle", boom)
        assert c.post("/api/chat", json={"question": "Como solicito férias?"}).status_code == 500
    entry = json.loads((tmp_path / "i.jsonl").read_text(encoding="utf-8"))
    assert entry["status"] == "error" and entry["error"] == "RuntimeError"
    assert "detalhe interno" not in json.dumps(entry)


def test_disabled_by_default_in_settings(tmp_path):
    assert Settings().interaction_log_path is None
    assert InteractionLog(None).enabled is False


def test_write_failure_does_not_break_chat(tmp_path):
    blocked = tmp_path / "arquivo"
    blocked.write_text("x", encoding="utf-8")  # um arquivo no lugar do diretório
    app = create_app(Settings(llm_provider="none", interaction_log_path=blocked / "i.jsonl"))
    with TestClient(app) as c:
        assert c.post("/api/chat", json={"question": "Como solicito férias?"}).status_code == 200


def test_env_var_controls_path(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_LOG_PATH", str(tmp_path / "x.jsonl"))
    assert Settings.from_env().interaction_log_path == tmp_path / "x.jsonl"
    monkeypatch.setenv("INTERACTION_LOG_PATH", "")
    assert Settings.from_env().interaction_log_path is None
