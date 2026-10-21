"""Conjunto de avaliação como teste de regressão (modo sem modelo, sem custo)."""
from app.agents.orchestrator import Orchestrator
from tests.eval.run_eval import evaluate, load_cases


def test_evaluation_set(retriever, no_llm):
    report = evaluate(Orchestrator(retriever, no_llm), load_cases())
    m = report["metricas"]
    official = [r for r in report["casos"] if r["origem"] == "pacote"]
    assert len(official) == 12
    assert all(r["roteamento"] for r in official), [r["id"] for r in official if not r["roteamento"]]
    for key in ("roteamento", "recuperacao", "fidelidade", "abstencao"):
        assert m[key]["taxa"] >= 0.9, (key, m[key])


def test_validation_set_is_measured_not_gated(retriever, no_llm):
    """O conjunto separado só é medido (sem limiar): ele não pode orientar ajustes."""
    from tests.eval.run_eval import load_validation_cases
    cases = load_validation_cases()
    assert len(cases) == 15 and all(c["origem"] == "validacao" for c in cases)
    report = evaluate(Orchestrator(retriever, no_llm), cases)
    assert set(report["metricas"]) == {"roteamento", "recuperacao", "fidelidade", "abstencao"}
