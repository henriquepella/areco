"""Fixtures compartilhadas. FakeLLM é um dublê de teste: nenhuma chamada paga é feita."""
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.document_service import load_knowledge_base
from app.services.llm_service import LLMError, LLMService
from app.services.retrieval_service import Retriever

KB_DIR = Path(__file__).resolve().parents[1] / "app" / "knowledge_base"


class FakeLLM(LLMService):
    """Simula o provedor: devolve `reply` (str), chama `reply(system, user)` ou levanta LLMError."""

    def __init__(self, reply=None, fail: bool = False):
        super().__init__(Settings(llm_provider="anthropic", llm_api_key="fixture-not-a-key"))
        self.reply, self.fail, self.calls = reply, fail, []

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        self.calls.append((system, user))
        if self.fail:
            raise LLMError("O provedor de IA não respondeu corretamente.")
        return self.reply(system, user) if callable(self.reply) else self.reply


@pytest.fixture(scope="session")
def retriever() -> Retriever:
    return Retriever(load_knowledge_base(KB_DIR))


@pytest.fixture
def no_llm() -> LLMService:
    return LLMService(Settings(llm_provider="none"))
