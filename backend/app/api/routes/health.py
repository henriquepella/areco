from fastapi import APIRouter, Request

router = APIRouter(tags=["saúde"])


@router.get("/health")
def health(request: Request) -> dict:
    """Disponibilidade do serviço, estado do índice e do provedor de IA (sem segredos)."""
    st = request.app.state
    indexed = st.retriever.status()["indexed"]
    return {"status": "ok" if indexed else "degraded", "knowledge_indexed": indexed,
            "llm": st.llm.status()}
