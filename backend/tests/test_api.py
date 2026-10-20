"""Endpoints HTTP com TestClient (sem rede, sem provedor de IA)."""
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app(Settings(llm_provider="none"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["knowledge_indexed"] is True
    assert body["llm"]["configured"] is False and "api_key" not in str(body).lower()


def test_health_degraded_without_kb(tmp_path):
    app = create_app(Settings(llm_provider="none", knowledge_dir=tmp_path))
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "degraded"


def test_chat_contract(client):
    r = client.post("/api/chat", json={"question": "Como solicito férias?"})
    assert r.status_code == 200
    b = r.json()
    assert b["category"] == "informacoes_internas"
    assert b["agent"] == "Agente de Informações Internas"
    assert b["status"] == "answered" and b["mode"] == "extractive"
    assert b["sources"][0]["document"] and b["sources"][0]["excerpt"]
    assert 0 < b["confidence"] <= 1
    assert set(b["routing"]) == {"method", "scores", "matched_terms", "reason"}


def test_chat_financial_value(client):
    r = client.post("/api/chat", json={"question": "Qual foi a soma de todas as receitas registradas em 2025?"})
    assert "R$ 275.727,70" in r.json()["answer"]


def test_chat_out_of_scope(client):
    b = client.post("/api/chat", json={"question": "Qual a previsão do tempo?"}).json()
    assert b["category"] == "indefinido" and b["status"] == "out_of_scope"


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}, {"pergunta": "x"},
                                     {"question": 123}])
def test_chat_validation(client, payload):
    r = client.post("/api/chat", json=payload)
    assert r.status_code == 422 and "detail" in r.json()


def test_chat_too_long(client):
    r = client.post("/api/chat", json={"question": "a" * 1001})
    assert r.status_code == 422 and "1000" in r.json()["detail"]


def test_chat_malformed_json(client):
    r = client.post("/api/chat", content="{nao-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_classify_endpoint(client):
    b = client.post("/api/classify", json={"question": "Qual o status do protocolo OUV-0002?"}).json()
    assert b["category"] == "ouvidoria" and "answer" not in b


def test_agents_endpoint(client):
    agents = client.get("/api/agents").json()
    assert [a["id"] for a in agents] == ["financeiro", "informacoes_internas", "ouvidoria"]
    assert "manifestacoes.csv" in agents[2]["knowledge"]


def test_knowledge_status(client):
    b = client.get("/api/knowledge/status").json()
    assert b["domains"]["informacoes_internas"]["documents"] == 11
    assert b["domains"]["ouvidoria"]["chunks"] > 0


def test_internal_error_hides_details(client, monkeypatch):
    def boom(_q):
        raise RuntimeError("segredo interno /caminho/arquivo.py")
    monkeypatch.setattr(client.app.state.orchestrator, "handle", boom)
    r = client.post("/api/chat", json={"question": "Como solicito férias?"})
    assert r.status_code == 500 and "segredo" not in r.text and "arquivo.py" not in r.text


def test_security_headers(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and "frame-ancestors" in h["content-security-policy"]
