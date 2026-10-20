"""Envia perguntas a uma instância EM EXECUÇÃO da API (local ou Docker), via HTTP.

O registro de interações é gravado pelo próprio servidor (INTERACTION_LOG_PATH), igual ao
uso pela interface. Usado para gerar docs/logs/interacoes_exemplo.jsonl.

Uso (a partir de backend/):
    python -m tests.eval.simular_interacoes                       # http://localhost:8000
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


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()

    questions = [c["pergunta"] for c in load_cases()] + EXTRAS
    with httpx.Client(base_url=args.url, timeout=60) as client:
        health = client.get("/health").json()
        print(f"Servidor: {args.url} | status={health['status']} | llm={health['llm']['provider']}")
        for q in questions:
            r = client.post("/api/chat", json={"question": q})
            b = r.json()
            print(f"{r.status_code} {b.get('category', '-'):21} {b.get('status', '-'):14} {q[:70]}")
    print(f"\n{len(questions)} perguntas enviadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
