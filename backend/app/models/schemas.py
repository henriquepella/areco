"""Contratos de entrada e saída da API (Pydantic)."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    method: Literal["rules", "llm", "guard"]
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
