"""Contratos de entrada e saída da API (Pydantic)."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Domain = Literal["financeiro", "informacoes_internas", "ouvidoria"]
Category = Literal["financeiro", "informacoes_internas", "ouvidoria", "indefinido"]
AnswerStatus = Literal["answered", "not_found", "clarification", "refused", "out_of_scope"]


class ChatRequest(BaseModel):
    # Limite final é aplicado na rota com MAX_QUESTION_CHARS; aqui só um teto absoluto.
    question: str = Field(..., min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def strip_and_check(cls, v: str) -> str:
        v = " ".join(v.split())  # remove quebras/espaços repetidos e caracteres de controle
        if not v:
            raise ValueError("a pergunta não pode ser vazia")
        return v


class Source(BaseModel):
    document: str
    domain: str
    section: str | None = None
    page: int | None = None
    excerpt: str
    score: float | None = None


class Routing(BaseModel):
    # context: domínio escolhido pelo usuário (menu) ou mantido da conversa, sem reclassificar
    method: Literal["rules", "llm", "guard", "context"]
    scores: dict[str, float]
    matched_terms: dict[str, list[str]]
    reason: str


class ClassificationResponse(BaseModel):
    question: str
    category: Category
    confidence: float | None
    routing: Routing


class AgentAnswer(BaseModel):
    answer: str
    status: AnswerStatus
    sources: list[Source] = []
    mode: Literal["llm", "extractive", "deterministic"] = "extractive"
    notice: str | None = None
    # Uso interno (não vai para /api/chat): parâmetro que faltou numa pergunta de esclarecimento.
    missing: Literal["ano", "protocolo"] | None = None


class ChatResponse(BaseModel):
    question: str
    category: Category
    agent: str | None
    answer: str
    status: AnswerStatus
    sources: list[Source]
    confidence: float | None = Field(
        None, description="Confiança do roteamento (regras). Nula quando não há estimativa confiável.")
    mode: Literal["llm", "extractive", "deterministic", "none"]
    routing: Routing
    notice: str | None = None


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str
    knowledge: list[str]


# ---------------------------------------------------------------- conversa guiada (/api/assist)

_SLOT_KEY = re.compile(r"[a-z_]{1,24}")


class AssistRequest(ChatRequest):
    """Mensagem da conversa guiada. Sem `domain`, a Aurora identifica o domínio."""
    domain: Domain | None = None
    intent: str | None = Field(None, max_length=40, pattern=r"^[a-z0-9_]+$")
    slots: dict[str, str] = Field(default_factory=dict)  # parâmetros já informados
    confirmed: bool = False  # o usuário já confirmou o domínio: não oferecer troca

    @field_validator("slots")
    @classmethod
    def check_slots(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 8 or any(not _SLOT_KEY.fullmatch(k) or len(val) > 1000 for k, val in v.items()):
            raise ValueError("parâmetros inválidos")
        return v


class Choice(BaseModel):
    """Alternativa clicável. Sem `question`, o clique só seleciona o domínio."""
    label: str
    question: str | None = None
    intent: str | None = None
    domain: Domain | None = None
    slots: dict[str, str] = {}
    confirmed: bool = False


class FollowUp(BaseModel):
    """O que a Aurora espera depois desta mensagem."""
    kind: Literal["slot", "confirm_intent", "confirm_domain", "switch_domain", "choose_domain", "suggest"]
    intent: str | None = None
    slot: str | None = None  # parâmetro pedido (kind="slot")
    slots: dict[str, str] = {}  # parâmetros já informados, devolvidos na próxima mensagem
    options: list[Choice] = []


class AssistResponse(ChatResponse):
    active_domain: Domain | None = None  # domínio em que a conversa continua
    intent: str | None = None
    follow_up: FollowUp | None = None


class Suggestion(BaseModel):
    id: str
    domain: Domain
    label: str
    needs: list[str]  # parâmetros que serão pedidos ao usuário


class SuggestionsResponse(BaseModel):
    domains: dict[str, list[Suggestion]]
