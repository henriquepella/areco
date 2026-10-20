"""Avaliação do roteamento, da recuperação, da fidelidade e da abstenção.

Uso (a partir de backend/):
    python -m tests.eval.run_eval            # usa LLM_PROVIDER do ambiente (padrão: none)
    python -m tests.eval.run_eval --save     # também grava tests/eval/resultados_<data>.json

Métricas (cada uma só sobre os casos em que se aplica):
- roteamento:  categoria prevista == domínio esperado ("fora_de_escopo_ou_segurança" aceita
               status refused/out_of_scope).
- recuperação: algum documento esperado aparece nas fontes devolvidas.
- fidelidade:  todos os termos de `deve_conter` aparecem na resposta (proxy por palavras-chave
               de que a resposta reproduz o que está nas fontes).
- abstenção:   para casos que exigem não responder (not_found, out_of_scope, refused,
               clarification), o status devolvido é o esperado.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABSTAIN = {"not_found", "out_of_scope", "refused", "clarification"}
SECURITY = "fora_de_escopo_ou_segurança"


def load_cases() -> list[dict]:
    official = json.loads((HERE / "perguntas_avaliacao.json").read_text(encoding="utf-8"))["casos"]
    extra = json.loads((HERE / "casos_adicionais.json").read_text(encoding="utf-8"))
    cases = []
    for c in official:
        cases.append({**c, **extra["complementos_oficiais"].get(c["id"], {}), "origem": "pacote"})
    cases += [{**c, "origem": "desenvolvedor"} for c in extra["casos"]]
    return cases


def evaluate(orchestrator, cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        r = orchestrator.handle(c["pergunta"])
        docs = {s.document for s in r.sources}
        expected_docs = {d.strip() for d in (c.get("fonte_esperada") or "").split(";") if d.strip()}
        if c["dominio_esperado"] == SECURITY:
            routing_ok = r.status in {"refused", "out_of_scope"}
        else:
            routing_ok = r.category == c["dominio_esperado"]
        retrieval_ok = bool(expected_docs & docs) if expected_docs and r.status != "refused" else None
        terms = c.get("deve_conter") or []
        faithful_ok = all(t.lower() in r.answer.lower() for t in terms) if terms else None
        exp_status = c.get("status_esperado")
        abstain_ok = (r.status == exp_status) if exp_status in ABSTAIN else None
        rows.append({"id": c["id"], "origem": c["origem"], "pergunta": c["pergunta"],
                     "esperado": c["dominio_esperado"], "previsto": r.category, "status": r.status,
                     "modo": r.mode, "fontes": sorted(docs), "roteamento": routing_ok,
                     "recuperacao": retrieval_ok, "fidelidade": faithful_ok, "abstencao": abstain_ok})

    def rate(key):
        vals = [row[key] for row in rows if row[key] is not None]
        return {"acertos": sum(vals), "total": len(vals),
                "taxa": round(sum(vals) / len(vals), 3) if vals else None}

    return {"metricas": {k: rate(k) for k in ("roteamento", "recuperacao", "fidelidade", "abstencao")},
            "casos": rows}


def _fmt(v):
    return "-" if v is None else ("ok" if v else "FALHA")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    from app.agents.orchestrator import Orchestrator
    from app.core.config import Settings
    from app.services.document_service import load_knowledge_base
    from app.services.llm_service import LLMService
    from app.services.retrieval_service import Retriever

    s = Settings.from_env()
    llm = LLMService(s)
    orch = Orchestrator(Retriever(load_knowledge_base(s.knowledge_dir)), llm, s.top_k)
    report = evaluate(orch, load_cases())
    report["provedor"] = llm.status()
    report["executado_em"] = datetime.now().isoformat(timespec="seconds")

    print(f"Provedor: {llm.status()['provider']} (configurado: {llm.status()['configured']})\n")
    print(f"{'id':5} {'esperado':22} {'previsto':22} {'status':14} rot   rec   fid   abs")
    for r in report["casos"]:
        print(f"{r['id']:5} {r['esperado'][:22]:22} {r['previsto']:22} {r['status']:14} "
              f"{_fmt(r['roteamento']):5} {_fmt(r['recuperacao']):5} {_fmt(r['fidelidade']):5} {_fmt(r['abstencao'])}")
    print()
    for k, m in report["metricas"].items():
        print(f"{k:12} {m['acertos']}/{m['total']}  ({m['taxa']})")
    if args.save:
        out = HERE / f"resultados_{datetime.now():%Y%m%d_%H%M%S}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nResultados salvos em {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
