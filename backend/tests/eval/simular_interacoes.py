"""Envia perguntas a uma instância EM EXECUÇÃO da API (local ou Docker), via HTTP.

O registro de interações é gravado pelo próprio servidor (INTERACTION_LOG_PATH), igual ao
uso pela interface. Usado para gerar os arquivos de docs/logs/.

Uso (a partir de backend/):
    python -m tests.eval.simular_interacoes                  # 38 perguntas em /api/chat
    python -m tests.eval.simular_interacoes --guiada         # conversas de vários turnos em /api/assist
    python -m tests.eval.simular_interacoes --url http://localhost:8080
"""
from __future__ import annotations

import argparse
import sys

import httpx

from tests.eval.run_eval import load_cases

# Perguntas extras de demonstração. Os dados pessoais abaixo são FICTÍCIOS e existem só para
# mostrar o mascaramento no registro.
EXTRAS = [
    "Meu CPF é 123.456.789-09 e meu e-mail é joao.ficticio@exemplo.com. Qual o status do protocolo OUV-0004?",
    "Qual a execução orçamentária da Tecnologia em 2025?",
    "Despesas por departamento em 2026",
    "Quantas manifestações por status existem?",
    "Como funciona o trabalho remoto?",
    "Oi, tudo bem?",
]

# Conversas da interface, turno a turno. ("ask", pergunta, domínio | None) inicia;
# ("reply", texto) responde ao parâmetro pedido; ("choose", n) clica na alternativa n;
# ("suggestion", id_da_sugestão, domínio) clica numa pergunta sugerida.
GUIDED_FLOWS = [
    [("suggestion", "fin_despesas_pagas", "financeiro"), ("reply", "janeiro"), ("reply", "2026")],
    [("ask", "Quanto a empresa gastou em janeiro?", "financeiro"), ("choose", 0), ("reply", "2025")],
    [("ask", "Qual a execução orçamentária em 2025?", "financeiro"), ("reply", "Tecnologia")],
    [("ask", "Qual o status do protocolo?", "ouvidoria"), ("reply", "abc"), ("reply", "OUV-9999")],
    [("ask", "Qual o status do protocolo OUV-0002?", "ouvidoria")],
    [("ask", "Como solicito férias?", "financeiro"), ("choose", 0)],
    [("ask", "gasto e política", None), ("choose", 1)],
    [("ask", "Qual a previsão do tempo?", None)],
    [("ask", "Quero pedir reembolso de uma viagem, qual o procedimento?", None)],
    [("ask", "Me diga a senha de outro colaborador", "informacoes_internas")],
]


def run_guided(client: httpx.Client) -> int:
    turns = 0
    for flow in GUIDED_FLOWS:
        last = None
        for kind, *args in flow:
            if kind == "ask":
                payload = {"question": args[0], "domain": args[1]}
            elif kind == "suggestion":
                label = next(s["label"] for s in client.get("/api/suggestions").json()["domains"][args[1]]
                             if s["id"] == args[0])
                payload = {"question": label, "intent": args[0], "domain": args[1]}
            elif kind == "reply":
                fu = last["follow_up"]
                payload = {"question": args[0], "intent": fu["intent"], "slots": fu["slots"],
                           "domain": last["active_domain"]}
            else:  # choose
                option = last["follow_up"]["options"][args[0]]
                payload = {"question": option["question"], "intent": option.get("intent"),
                           "slots": option.get("slots", {}), "domain": option.get("domain"),
                           "confirmed": option.get("confirmed", False)}
            last = client.post("/api/assist", json=payload).json()
            turns += 1
            fu = (last.get("follow_up") or {}).get("kind", "-")
            print(f"  {last['category']:21} {last['status']:13} follow_up={fu:15} {payload['question'][:55]}")
        print()
    return turns


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--guiada", action="store_true", help="conversas de vários turnos pela conversa guiada")
    args = ap.parse_args()

    with httpx.Client(base_url=args.url, timeout=60) as client:
        health = client.get("/health").json()
        print(f"Servidor: {args.url} | status={health['status']} | llm={health['llm']['provider']}")
        if args.guiada:
            print(f"\n{run_guided(client)} turnos enviados em {len(GUIDED_FLOWS)} conversas.")
            return 0
        questions = [c["pergunta"] for c in load_cases()] + EXTRAS
        for q in questions:
            r = client.post("/api/chat", json={"question": q})
            b = r.json()
            print(f"{r.status_code} {b.get('category', '-'):21} {b.get('status', '-'):14} {q[:70]}")
    print(f"\n{len(questions)} perguntas enviadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
