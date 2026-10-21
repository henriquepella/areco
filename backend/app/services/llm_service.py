"""Abstração do provedor de IA. A lógica de negócio só chama `LLMService.complete`.

Provedores (LLM_PROVIDER):
- none      -> sem modelo; os agentes respondem em modo extrativo/determinístico.
- anthropic -> Claude via SDK oficial `anthropic`.
- openai    -> qualquer API compatível com /chat/completions (OpenAI, Ollama, OpenRouter...).
"""
from __future__ import annotations

import logging
import re

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5"}
SUPPORTED = {"none", "anthropic", "openai"}


class LLMError(RuntimeError):
    """Falha ao falar com o provedor. A mensagem é segura para exibir ao usuário."""


def describe_error(exc: Exception, secret: str | None = None) -> str:
    """Resumo da falha sem dados sensíveis: tipo da exceção, código HTTP, tipo e mensagem de erro
    da API (ex.: "BadRequestError HTTP 400 invalid_request_error: <motivo>"). A mensagem vem só do
    campo error.message do corpo (nunca cabeçalhos), truncada e com qualquer chave mascarada."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    api_type = error.get("type") or getattr(exc, "type", None)
    parts = [type(exc).__name__, f"HTTP {status}" if status else None,
             api_type if isinstance(api_type, str) else None]
    text = " ".join(p for p in parts if p)
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        if secret:
            message = message.replace(secret, "[chave]")
        text += ": " + re.sub(r"sk-[\w-]+", "[chave]", message.strip())[:200]
    return text


class LLMService:
    def __init__(self, settings: Settings):
        self.s = settings
        self.provider = settings.llm_provider if settings.llm_provider in SUPPORTED else "invalid"
        self.model = settings.llm_model or DEFAULT_MODELS.get(self.provider, "")
        self._client = None
        self.last_error: str | None = None  # diagnóstico seguro da última falha (sem segredos)

    @property
    def available(self) -> bool:
        return self.status()["configured"]

    def status(self) -> dict:
        reason = None
        if self.provider == "invalid":
            reason = f"LLM_PROVIDER inválido: use um de {sorted(SUPPORTED)}"
        elif self.provider == "none":
            reason = "LLM_PROVIDER=none: modo extrativo (sem modelo de linguagem)"
        elif self.provider == "anthropic" and not self.s.llm_api_key:
            reason = "LLM_API_KEY não configurada"
        elif self.provider == "openai" and not self.model:
            reason = "LLM_MODEL não configurado"
        elif self.provider == "openai" and not (self.s.llm_api_key or self.s.llm_base_url):
            reason = "LLM_API_KEY não configurada (ou defina LLM_BASE_URL para um servidor local)"
        return {"provider": self.provider, "model": self.model or None,
                "configured": reason is None, "reason": reason}

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        if not self.available:
            raise LLMError("Provedor de IA não configurado.")
        try:
            text = self._anthropic(system, user, max_tokens) if self.provider == "anthropic" \
                else self._openai(system, user, max_tokens)
        except LLMError as exc:
            self.last_error = str(exc)
            raise
        except Exception as exc:  # noqa: BLE001 - qualquer falha do provedor vira LLMError
            # Só tipo, código HTTP e error.message da API (sem a chave) vão para o log; a mensagem
            # da exceção e o resto da resposta podem conter cabeçalhos ou segredos.
            self.last_error = describe_error(exc, self.s.llm_api_key)
            logger.error("llm_call_failed", extra={"provider": self.provider, "error": self.last_error})
            raise LLMError("O provedor de IA não respondeu corretamente.") from exc
        if not text.strip():
            self.last_error = "resposta vazia"
            raise LLMError("O provedor de IA retornou uma resposta vazia.")
        self.last_error = None
        return text.strip()

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self.s.llm_api_key, base_url=self.s.llm_base_url or None,
                timeout=float(self.s.llm_timeout_s), max_retries=1)
        msg = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        if msg.stop_reason == "refusal":
            raise LLMError("O modelo recusou a solicitação.")
        return "".join(b.text for b in msg.content if b.type == "text")

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        base = (self.s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}"} if self.s.llm_api_key else {}
        r = httpx.post(f"{base}/chat/completions", headers=headers, timeout=self.s.llm_timeout_s,
                       json={"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""
