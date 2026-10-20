"""Rotas do assistente: conversa, conversa guiada, sugestões, classificação isolada, agentes e
estado da base."""
import time
from typing import Callable

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (AgentInfo, AssistRequest, AssistResponse, ChatRequest, ChatResponse,
                                ClassificationResponse, SuggestionsResponse)

router = APIRouter(prefix="/api", tags=["assistente"])


def _check_length(request: Request, question: str) -> None:
    limit = request.app.state.settings.max_question_chars
    if len(question) > limit:
        raise HTTPException(status_code=422, detail=f"A pergunta excede o limite de {limit} caracteres.")


def _logged(request: Request, question: str, handler: Callable[[], ChatResponse], endpoint: str) -> ChatResponse:
    """Executa a pergunta e grava a interação (ou só o tipo do erro) no registro de interações."""
    log = request.app.state.interactions
    started = time.perf_counter()

    def elapsed_ms() -> float:
        return (time.perf_counter() - started) * 1000

    try:
        resp = handler()
    except Exception as exc:
        # Registra só o tipo do erro; o handler global devolve 500 sem detalhes internos.
        log.record(None, question, elapsed_ms(), type(exc).__name__, endpoint=endpoint)
        raise
    log.record(resp, question, elapsed_ms(), endpoint=endpoint)
    return resp


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Classifica a pergunta, encaminha ao agente e devolve resposta, categoria e fontes."""
    _check_length(request, body.question)
    return _logged(request, body.question, lambda: request.app.state.orchestrator.handle(body.question), "chat")


@router.post("/assist", response_model=AssistResponse)
def assist(body: AssistRequest, request: Request) -> AssistResponse:
    """Conversa guiada: considera o domínio escolhido, confirma intenções ambíguas e pede os
    parâmetros que faltam antes de encaminhar ao agente (estado devolvido em `follow_up`)."""
    _check_length(request, body.question)
    return _logged(request, body.question, lambda: request.app.state.assistant.respond(body), "assist")


@router.get("/suggestions", response_model=SuggestionsResponse)
def suggestions(request: Request) -> SuggestionsResponse:
    """Perguntas sugeridas por domínio, geradas a partir dos arquivos carregados."""
    return SuggestionsResponse(domains=request.app.state.assistant.catalog.suggestions())


@router.post("/classify", response_model=ClassificationResponse)
def classify(body: ChatRequest, request: Request) -> ClassificationResponse:
    """Só a classificação do orquestrador, sem gerar resposta (útil para avaliar o roteamento)."""
    _check_length(request, body.question)
    return request.app.state.orchestrator.classify(body.question)


@router.get("/agents", response_model=list[AgentInfo])
def agents(request: Request) -> list[AgentInfo]:
    return [AgentInfo(id=a.id, name=a.name, description=a.description, knowledge=a.knowledge())
            for a in request.app.state.orchestrator.agents.values()]


@router.get("/knowledge/status")
def knowledge_status(request: Request) -> dict:
    return request.app.state.retriever.status()
