"""Ponto de entrada FastAPI. Uso: `uvicorn app.main:app` a partir de `backend/`."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.orchestrator import Orchestrator
from app.api.routes import chat, health
from app.core.config import Settings
from app.core.logging_config import setup_logging
from app.services.document_service import load_knowledge_base
from app.services.llm_service import LLMService
from app.services.retrieval_service import Retriever

logger = logging.getLogger("app")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"),
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        kb = load_knowledge_base(settings.knowledge_dir, settings.max_file_bytes)
        app.state.settings = settings
        app.state.retriever = Retriever(kb)
        app.state.llm = LLMService(settings)
        app.state.orchestrator = Orchestrator(app.state.retriever, app.state.llm, settings.top_k)
        status = app.state.retriever.status()
        logger.info("knowledge_loaded", extra={
            d: v["documents"] for d, v in status["domains"].items()})
        logger.info("llm_status", extra=app.state.llm.status())
        yield

    app = FastAPI(title="Assistente Corporativo Multidomínio", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()})
        return JSONResponse(status_code=422, content={
            "detail": "Requisição inválida. Envie JSON no formato {\"question\": \"...\"} "
                      "com uma pergunta não vazia.", "fields": fields})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.error("unhandled_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=500, content={
            "detail": "Erro interno ao processar a solicitação. Tente novamente."})

    app.include_router(health.router)
    app.include_router(chat.router)
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


app = create_app()
