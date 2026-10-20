"""Configuração lida de variáveis de ambiente. Nenhum segredo fica no código."""
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KB_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "none"  # none | anthropic | openai
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)  # repr=False: nunca aparece em logs
    llm_base_url: str = ""
    llm_timeout_s: int = 30
    knowledge_dir: Path = DEFAULT_KB_DIR
    frontend_dir: Path = DEFAULT_FRONTEND_DIR
    max_question_chars: int = 1000
    max_file_bytes: int = 5 * 1024 * 1024
    top_k: int = 4
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "none").strip().lower() or "none",
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
            llm_timeout_s=_int("LLM_TIMEOUT_SECONDS", 30),
            knowledge_dir=Path(os.getenv("KNOWLEDGE_DIR", str(DEFAULT_KB_DIR))),
            frontend_dir=Path(os.getenv("FRONTEND_DIR", str(DEFAULT_FRONTEND_DIR))),
            max_question_chars=_int("MAX_QUESTION_CHARS", 1000),
            top_k=_int("RETRIEVAL_TOP_K", 4),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
