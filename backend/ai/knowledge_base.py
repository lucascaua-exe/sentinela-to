"""
Sentinela TO - Base de conhecimento técnica para o RAG

Carrega os documentos Markdown de backend/ai/knowledge, divide cada um em trechos
(seções "## ") e indexa os trechos num espaço vetorial TF-IDF (scikit-learn). A busca
por similaridade de cosseno devolve os trechos mais relevantes para a anomalia.

Formato de cada seção:
    ## Título
    ref: referência normativa exibida ao operador
    tags: códigos de diagnóstico e palavras-chave
    causa: (opcional) causa provável, usada pelo compositor local
    imediata: / preventiva: (opcionais, repetíveis) ações recomendadas
    texto livre...
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
FIELD_RE = re.compile(r"^(ref|tags|causa|imediata|preventiva):\s*(.+)$")
VALIDATED_CATEGORY = "validated"


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass
class Chunk:
    id: str
    source: str
    category: str
    title: str
    ref: str
    tags: list[str]
    text: str
    cause: str = ""
    immediate: list[str] = field(default_factory=list)
    preventive: list[str] = field(default_factory=list)

    @property
    def validated(self) -> bool:
        return self.category == VALIDATED_CATEGORY

    def indexed_text(self) -> str:
        parts = [self.title, " ".join(self.tags), self.text, self.cause, *self.immediate, *self.preventive]
        return " ".join(parts)

    def as_context(self) -> str:
        return f"[{self.ref}] {self.title}: {self.text}"


@dataclass
class Retrieved:
    chunk: Chunk
    score: float


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if raw.startswith("---"):
        _, header, body = raw.split("---", 2)
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        return meta, body
    return meta, raw


def parse_document(path: Path) -> list[Chunk]:
    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    chunks = []
    for i, section in enumerate(re.split(r"^## ", body, flags=re.MULTILINE)[1:]):
        lines = section.strip().splitlines()
        fields: dict[str, list[str]] = {}
        text_lines = []
        for line in lines[1:]:
            match = FIELD_RE.match(line.strip())
            if match:
                fields.setdefault(match.group(1), []).append(match.group(2).strip())
            elif line.strip():
                text_lines.append(line.strip())
        chunks.append(
            Chunk(
                id=f"{path.stem}#{i}",
                source=meta.get("source", path.stem),
                category=meta.get("category", ""),
                title=lines[0].strip(),
                ref=fields.get("ref", [lines[0].strip()])[0],
                tags=" ".join(fields.get("tags", [])).split(),
                text=" ".join(text_lines),
                cause=fields.get("causa", [""])[0],
                immediate=fields.get("imediata", []),
                preventive=fields.get("preventiva", []),
            )
        )
    return chunks


@dataclass(frozen=True)
class _Index:
    chunks: list[Chunk]
    vectorizer: TfidfVectorizer
    matrix: object
    fingerprint: tuple
    version: int


def _fingerprint(directory: Path) -> tuple:
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(directory.glob("*.md")))


class KnowledgeBase:
    """Índice vetorial com reindexação a quente: o índice novo é montado à parte e trocado numa única atribuição."""

    def __init__(self, directory: Path = KNOWLEDGE_DIR, extra: list[Chunk] | None = None) -> None:
        self.directory = directory
        self._extra: list[Chunk] = list(extra or [])
        self._index = self._build(version=1)

    def _build(self, version: int) -> _Index:
        documents = [c for path in sorted(self.directory.glob("*.md")) for c in parse_document(path)]
        if not documents:
            raise RuntimeError(f"Base de conhecimento vazia em {self.directory}")
        chunks = documents + self._extra
        vectorizer = TfidfVectorizer(preprocessor=normalize, ngram_range=(1, 2), sublinear_tf=True)
        matrix = vectorizer.fit_transform([c.indexed_text() for c in chunks])
        return _Index(chunks, vectorizer, matrix, _fingerprint(self.directory), version)

    def reindex(self, extra: list[Chunk] | None = None) -> int:
        """Reconstrói o índice (documentos + trechos validados) sem interromper as buscas em andamento."""
        if extra is not None:
            self._extra = list(extra)
        self._index = self._build(self._index.version + 1)
        return self._index.version

    def refresh_if_changed(self) -> bool:
        """Reindexa se algum documento técnico foi adicionado ou alterado no repositório da base."""
        if _fingerprint(self.directory) == self._index.fingerprint:
            return False
        self.reindex()
        return True

    @property
    def chunks(self) -> list[Chunk]:
        return self._index.chunks

    @property
    def version(self) -> int:
        return self._index.version

    @property
    def sources(self) -> list[str]:
        return sorted({c.source for c in self.chunks})

    def similarities(self, query: str) -> tuple[list[Chunk], "object"]:
        """Similaridade de cosseno bruta da consulta com todos os trechos (mesmo snapshot do índice)."""
        index = self._index
        return index.chunks, linear_kernel(index.vectorizer.transform([query]), index.matrix).ravel()

    def search(self, query: str, k: int = 4, codes: set[str] | None = None) -> list[Retrieved]:
        """Top-k por similaridade de cosseno; trechos marcados com o código do diagnóstico recebem prioridade."""
        chunks, raw = self.similarities(query)
        scores = raw.copy()
        if codes:
            for i, chunk in enumerate(chunks):
                if codes & set(chunk.tags):
                    scores[i] += 0.15
        ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        return [Retrieved(chunks[i], float(scores[i])) for i in ranked[:k] if scores[i] > 0]
