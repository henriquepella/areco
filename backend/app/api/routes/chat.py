import time

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import AgentInfo, ChatRequest, ChatResponse, ClassificationResponse

router = APIRouter(prefix="/api", tags=["assistente"])


def _check_length(request: Request, question: str) -> None:
    limit = request.app.state.settings.max_question_chars
    if len(question) > limit:
        raise HTTPException(status_code=422, detail=f"A pergunta excede o limite de {limit} caracteres.")


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Classifica a pergunta, encaminha ao agente e devolve resposta, categoria e fontes."""
    _check_length(request, body.question)
    log = request.app.state.interactions
    started = time.perf_counter()
    try:
        resp = request.app.state.orchestrator.handle(body.question)
    except Exception as exc:
        log.record(None, body.question, (time.perf_counter() - started) * 1000, type(exc).__name__)
        raise
    log.record(resp, body.question, (time.perf_counter() - started) * 1000)
    return resp


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
