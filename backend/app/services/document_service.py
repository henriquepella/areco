"""Leitura, validação e divisão em trechos (chunks) dos arquivos de conhecimento.

Regra de domínio: a subpasta do arquivo define o domínio
(`knowledge_base/<dominio>/<arquivo>`). Arquivos fora dessas pastas não são indexados.
Os arquivos são tratados como dados não confiáveis: só o texto é extraído,
nada é executado e instruções contidas neles não alteram o comportamento do sistema.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOMAINS = ("financeiro", "informacoes_internas", "ouvidoria")
ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json"}
MAX_CHUNK_CHARS = 1500
# Colunas constantes (avisos de dado sintético) que só adicionam ruído à busca.
CSV_SKIP_COLUMNS = {"observacao", "dados_pessoais"}


@dataclass
class Chunk:
    id: str
    domain: str
    document: str
    text: str
    section: str | None = None
    page: int | None = None  # nenhum anexo é paginado (não há PDF); mantido no contrato
    version: str | None = None


@dataclass
class FileStatus:
    document: str
    domain: str | None
    status: str  # indexed | skipped | error
    chunks: int = 0
    reason: str | None = None


@dataclass
class KnowledgeBase:
    chunks: dict[str, list[Chunk]] = field(default_factory=lambda: {d: [] for d in DOMAINS})
    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # nome do CSV -> linhas
    json_docs: dict[str, Any] = field(default_factory=dict)  # nome do JSON -> conteúdo
    files: list[FileStatus] = field(default_factory=list)
    loaded_at: str = ""


class DocumentError(ValueError):
    """Arquivo inválido (vazio, formato não suportado ou conteúdo malformado)."""


# ---------------------------------------------------------------- parsers

_VERSION_RE = re.compile(r"Vers[aã]o\s+([\d.]+)\s*[—–-]\s*([\d/]+)", re.IGNORECASE)


def _split_long(text: str) -> list[str]:
    """Divide textos longos por parágrafos, com sobreposição de um parágrafo."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    parts, current = [], []
    for p in paragraphs:
        if current and len("\n\n".join(current + [p])) > MAX_CHUNK_CHARS:
            parts.append("\n\n".join(current))
            current = current[-1:]  # sobreposição moderada
        current.append(p)
    if current:
        parts.append("\n\n".join(current))
    return parts


def parse_markdown(text: str) -> tuple[list[tuple[str | None, str]], str | None]:
    """Retorna [(seção, texto)] e a versão do documento. Divide por títulos `##`+."""
    m = _VERSION_RE.search(text)
    version = f"{m.group(1)} ({m.group(2)})" if m else None
    title, intro, sections = None, [], []
    heading, body = None, []
    for line in text.splitlines():
        h = re.match(r"^(#{1,6})\s+(.*)", line)
        if h and len(h.group(1)) == 1 and title is None:
            title = h.group(2).strip()
            continue
        if h:
            if heading is not None:
                sections.append((heading, "\n".join(body).strip()))
            heading, body = h.group(2).strip(), []
            continue
        (body if heading is not None else intro).append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body).strip()))

    intro_text = "\n".join(intro).strip()
    if not sections:
        sections = [(None, intro_text)]
    elif intro_text:  # o cabeçalho (aviso + versão) vai junto da primeira seção
        sections[0] = (sections[0][0], f"{intro_text}\n\n{sections[0][1]}")

    out = []
    for sec, body_text in sections:
        if not body_text:
            continue
        prefix = " — ".join(x for x in (title, sec) if x)
        for part in _split_long(body_text):
            out.append((sec, f"{prefix}\n{part}" if prefix else part))
    return out, version


def _render(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k.replace('_', ' ')}: {_render(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(_render(v) for v in value)
    return str(value)


def parse_json(data: Any) -> list[tuple[str | None, str]]:
    """Lista de objetos -> um trecho por item. Objeto -> um trecho por chave composta."""
    if isinstance(data, list):
        return [
            (str(item.get("id", i)) if isinstance(item, dict) else str(i), _render(item))
            for i, item in enumerate(data)
        ]
    if not isinstance(data, dict):
        return [(None, _render(data))]
    out, scalars = [], {}
    for key, value in data.items():
        label = key.replace("_", " ")
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            out += [(key, f"{label}: {_render(v)}") for v in value]
        elif isinstance(value, (list, dict)):
            out.append((key, f"{label}: {_render(value)}"))
        else:
            scalars[key] = value
    if scalars:
        out.insert(0, ("geral", _render(scalars)))
    return out


def parse_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DocumentError("CSV sem cabeçalho")
    rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]
    if not rows:
        raise DocumentError("CSV sem registros")
    return rows


def _row_text(row: dict[str, str]) -> str:
    return "; ".join(
        f"{k.replace('_', ' ')}: {v}" for k, v in row.items() if v and k not in CSV_SKIP_COLUMNS
    )


# ---------------------------------------------------------------- carga


def read_text(path: Path, max_bytes: int) -> str:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise DocumentError(f"formato não suportado: {path.suffix or 'sem extensão'}")
    size = path.stat().st_size
    if size == 0:
        raise DocumentError("arquivo vazio")
    if size > max_bytes:
        raise DocumentError("arquivo excede o tamanho máximo permitido")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError("arquivo não está em UTF-8") from exc
    if not text.strip():
        raise DocumentError("arquivo vazio")
    return text


def load_file(path: Path, domain: str, kb: KnowledgeBase, max_bytes: int) -> int:
    """Carrega um arquivo na base; retorna a quantidade de trechos gerados."""
    text = read_text(path, max_bytes)
    ext, name = path.suffix.lower(), path.name
    version = None
    if ext in {".md", ".txt"}:
        pieces, version = parse_markdown(text)
    elif ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DocumentError("JSON inválido") from exc
        kb.json_docs[name] = data
        pieces = parse_json(data)
    else:  # .csv
        rows = parse_csv(text)
        kb.tables[name] = rows
        pieces = [(next(iter(r.values()), None), _row_text(r)) for r in rows]

    pieces = [(s, t) for s, t in pieces if t.strip()]
    if not pieces:
        raise DocumentError("nenhum conteúdo textual extraído")
    for i, (section, chunk_text) in enumerate(pieces):
        kb.chunks[domain].append(
            Chunk(id=f"{name}#{i}", domain=domain, document=name, text=chunk_text,
                  section=section, version=version)
        )
    return len(pieces)


def load_knowledge_base(root: Path, max_bytes: int = 5 * 1024 * 1024) -> KnowledgeBase:
    kb = KnowledgeBase(loaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if not root.is_dir():
        logger.error("knowledge_dir_missing", extra={"path": str(root)})
        return kb
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # Só arquivos diretamente dentro de uma pasta de domínio; bloqueia links que saiam da base.
        domain = rel.parts[0] if len(rel.parts) == 2 else None
        if domain not in DOMAINS:
            kb.files.append(FileStatus(str(rel.as_posix()), None, "skipped",
                                       reason="fora de uma pasta de domínio"))
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            kb.files.append(FileStatus(path.name, domain, "skipped", reason="link não permitido"))
            continue
        try:
            n = load_file(path, domain, kb, max_bytes)
            kb.files.append(FileStatus(path.name, domain, "indexed", chunks=n))
        except (DocumentError, OSError) as exc:
            reason = str(exc) if isinstance(exc, DocumentError) else "erro de leitura"
            kb.files.append(FileStatus(path.name, domain, "error", reason=reason))
            logger.warning("document_rejected", extra={"document": path.name, "reason": reason})
    return kb
