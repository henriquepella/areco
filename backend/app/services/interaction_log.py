"""Registro das interações com o agente em JSON Lines (uma interação por linha).

Cada linha guarda o que é preciso para auditar o roteamento e as respostas: pergunta
(com dados pessoais mascarados), categoria, agente, status, modo, confiança, fontes,
resposta (truncada) e latência. Falhas de escrita nunca interrompem o atendimento.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.schemas import ChatResponse

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 1200

# Ordem importa: segredos e e-mails antes dos padrões numéricos.
_MASKS = [
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{16,}|xox[abp]-[A-Za-z0-9-]{10,})\b"), "[segredo]"),
    (re.compile(r"(?i)\b(senha|password|token|chave)\s*[:=]\s*\S+"), r"\1: [segredo]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[cpf]"),
    (re.compile(r"\b\d{13,19}\b"), "[numero]"),
    (re.compile(r"(\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b"), "[telefone]"),
]


def mask_personal_data(text: str) -> str:
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text


class InteractionLog:
    """Anexa interações a um arquivo .jsonl. `path=None` desliga o registro."""

    def __init__(self, path: Path | None, provider: str = "none"):
        self.path = path
        self.provider = provider
        # ponytail: um arquivo e um lock global; trocar por rotação/fila se o volume crescer.
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def record(self, resp: ChatResponse | None, question: str, latency_ms: float,
               error: str | None = None) -> None:
        if not self.enabled:
            return
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "question": mask_personal_data(question),
            "question_chars": len(question),
            "latency_ms": round(latency_ms, 1),
            "llm_provider": self.provider,
        }
        if resp is not None:
            # Só a pergunta é texto livre do usuário. A resposta vem das bases (sem dados
            # pessoais) e mascará-la apagaria canais oficiais como seguranca@aurora.example.
            answer = resp.answer
            entry.update({
                "category": resp.category,
                "agent": resp.agent,
                "status": resp.status,
                "mode": resp.mode,
                "confidence": resp.confidence,
                "routing_method": resp.routing.method,
                "routing_scores": resp.routing.scores,
                "sources": [{"document": s.document, "section": s.section} for s in resp.sources],
                "answer": answer if len(answer) <= MAX_ANSWER_CHARS else answer[:MAX_ANSWER_CHARS] + "…",
                "notice": resp.notice,
            })
        if error:
            entry.update({"status": "error", "error": error})
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as exc:
            logger.warning("interaction_log_write_failed", extra={"error_type": type(exc).__name__})
