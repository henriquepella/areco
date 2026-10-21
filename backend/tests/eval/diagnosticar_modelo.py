"""Testa a conexão com o provedor de IA configurado (LLM_PROVIDER, LLM_MODEL, LLM_API_KEY...).

Faz uma chamada mínima e informa se o modelo respondeu. Em caso de falha mostra só o tipo do
erro, o código HTTP e o tipo de erro da API — nunca a chave nem o corpo da resposta.
No GitHub Actions, o resultado também vira uma anotação do job (legível na página da execução).

Uso (a partir de backend/):  python -m tests.eval.diagnosticar_modelo
"""
from __future__ import annotations

import os
import sys
import time

from app.core.config import Settings
from app.services.llm_service import LLMError, LLMService


def annotate(level: str, title: str, message: str) -> None:
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::{level} title={title}::{message}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    svc = LLMService(Settings.from_env())
    status = svc.status()
    print(f"Provedor: {status['provider']} | modelo: {status['model']} | configurado: {status['configured']}")
    if not status["configured"]:
        annotate("error", "Modelo não configurado", status["reason"] or "configuração incompleta")
        print(f"FALHA: {status['reason']}")
        return 1
    started = time.perf_counter()
    try:
        text = svc.complete("Responda somente com a palavra OK.", "Teste de conexão.", max_tokens=1000)
    except LLMError as exc:
        detail = svc.last_error or str(exc)
        annotate("error", "Modelo indisponível", f"{status['provider']}/{status['model']}: {detail}")
        print(f"FALHA: {detail}")
        return 1
    elapsed = time.perf_counter() - started
    annotate("notice", "Modelo respondeu", f"{status['provider']}/{status['model']} em {elapsed:.1f} s")
    print(f"OK: o modelo respondeu em {elapsed:.1f} s ({len(text)} caracteres)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
