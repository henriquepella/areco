"""Busca textual BM25, com um índice separado por domínio.

Escolha: BM25 puro em Python (sem dependência extra). A base tem ~670 trechos curtos;
o índice inteiro é reconstruído em memória na inicialização em milissegundos, então
não há necessidade de banco vetorial nem de persistir o índice em disco.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from app.services.document_service import DOMAINS, Chunk, KnowledgeBase

STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "em",
    "no", "na", "nos", "nas", "por", "pelo", "pela", "para", "pra", "com", "sem", "e", "ou",
    "que", "se", "ao", "aos", "qual", "quais", "como", "quando", "onde", "quem", "me", "meu",
    "minha", "eu", "voce", "ele", "ela", "isso", "esse", "essa", "este", "esta", "ha", "ser",
    "sao", "foi", "tem", "ter", "existe", "sobre", "mais", "menos", "muito", "ja", "nao",
    "sim", "the", "of", "favor", "gostaria", "saber", "preciso", "posso", "pode", "devo",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _stem(token: str) -> str:
    # ponytail: stemmer de sufixos mínimo para PT-BR; trocar por RSLP/Snowball se a base crescer.
    for suffix, repl in (("coes", "cao"), ("oes", "ao"), ("aes", "ao"), ("ais", "al")):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)] + repl
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", normalize(text))
            if t not in STOPWORDS and len(t) > 1]


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    coverage: float  # fração dos termos da pergunta presentes no trecho


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c.text)) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avgdl = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df = Counter(term for d in self.docs for term in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 4) -> list[SearchHit]:
        terms = set(tokenize(query))
        if not terms or not self.docs:
            return []
        hits = []
        for chunk, tf, dl in zip(self.chunks, self.docs, self.lengths):
            matched = [t for t in terms if t in tf]
            if not matched:
                continue
            score = 0.0
            for t in matched:
                f = tf[t]
                score += self.idf[t] * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            hits.append(SearchHit(chunk, round(score, 4), round(len(matched) / len(terms), 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


class Retriever:
    """Um índice por domínio: um agente nunca recebe trechos de outro domínio."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.indexes = {d: BM25Index(kb.chunks.get(d, [])) for d in DOMAINS}

    def search(self, domain: str, query: str, k: int = 4) -> list[SearchHit]:
        if domain not in self.indexes:
            raise ValueError(f"domínio desconhecido: {domain}")
        return self.indexes[domain].search(query, k)

    def status(self) -> dict:
        per_domain = {}
        for d in DOMAINS:
            files = [f for f in self.kb.files if f.domain == d]
            per_domain[d] = {
                "documents": sum(1 for f in files if f.status == "indexed"),
                "chunks": len(self.kb.chunks.get(d, [])),
                "files": [f.__dict__ for f in files],
            }
        return {
            "indexed": any(v["chunks"] for v in per_domain.values()),
            "loaded_at": self.kb.loaded_at,
            "retrieval": "BM25 (índice em memória por domínio)",
            "domains": per_domain,
            "ignored_files": [f.__dict__ for f in self.kb.files if f.domain is None],
        }
