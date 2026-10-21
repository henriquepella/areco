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
    "fazer", "faco", "faz", "houver", "acontece", "acontecer", "caso", "algum", "alguma",
    # verbos genéricos de pedido ("como faço para tirar férias?"): não dizem o assunto
    "tirar", "obter", "conseguir", "consigo", "quero", "queria", "pedir",
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
    coverage: float  # fração do peso IDF dos termos da pergunta presente no trecho
    terms: frozenset[str] = frozenset()  # termos da pergunta encontrados no trecho


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
        # Termo ausente do índice recebe o IDF máximo: pesa contra a cobertura.
        self.unseen_idf = math.log(1 + (n + 0.5) / 0.5)

    def search(self, query: str, k: int = 4) -> list[SearchHit]:
        terms = set(tokenize(query))
        if not terms or not self.docs:
            return []
        total_idf = sum(self.idf.get(t, self.unseen_idf) for t in terms)
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
            coverage = sum(self.idf[t] for t in matched) / total_idf
            hits.append(SearchHit(chunk, round(score, 4), round(coverage, 3), frozenset(matched)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


class Retriever:
    """Um índice por domínio: um agente nunca recebe trechos de outro domínio."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.indexes = {d: BM25Index(kb.chunks.get(d, [])) for d in DOMAINS}
        self._title_terms = self._distinctive_title_terms()

    def _distinctive_title_terms(self) -> dict[str, set[str]]:
        """Termos do título de cada documento Markdown que não aparecem no título de nenhum
        outro ("reembolso" em "Política de reembolso"; "política" é comum e fica de fora)."""
        titles = {}
        for chunks in self.kb.chunks.values():
            for c in chunks:
                if c.document.endswith((".md", ".txt")) and c.document not in titles:
                    titles[c.document] = set(tokenize(c.text.split("\n", 1)[0].split(" — ")[0]))
        count = Counter(t for terms in titles.values() for t in terms)
        return {doc: {t for t in terms if count[t] == 1} for doc, terms in titles.items()}

    def anchor_terms(self, hit: SearchHit) -> set[str]:
        """Termos da pergunta que nomeiam o assunto do documento do trecho (pelo título).
        Itens da FAQ usam o título do documento que citam ("documento: 02_politica_reembolso.md")."""
        doc = hit.chunk.document
        if doc not in self._title_terms:
            m = re.search(r"documento: (\S+\.(?:md|txt))", hit.chunk.text)
            doc = m.group(1) if m else doc
        return set(hit.terms) & self._title_terms.get(doc, set())

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
