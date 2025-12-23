"""Retrieval store primitives and in-memory implementation."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkEmbedding:
    """Chunk payload stored in the retrieval index."""

    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, object]
    embedding: list[float]


@dataclass(frozen=True)
class RetrievedChunk:
    """Result returned from a retrieval query."""

    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, object]
    score: float


class RetrievalStore(ABC):
    """Interface describing required retrieval operations."""

    @abstractmethod
    def add_chunks(self, chunks: Sequence[ChunkEmbedding]) -> None:
        """Add chunk embeddings to the store."""

    @abstractmethod
    def query(self, text: str, top_k: int = 3) -> list[RetrievedChunk]:
        """Retrieve the most similar chunks for the provided text."""


def _vector_norm(values: Sequence[float]) -> float:
    return math.sqrt(sum(component * component for component in values))


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class InMemoryRetrievalStore(RetrievalStore):
    """Simple cosine-similarity store held entirely in memory."""

    def __init__(self, embed_text: Callable[[str], list[float]]):
        self._embed_text = embed_text
        self._chunks: list[ChunkEmbedding] = []
        self._norms: list[float] = []

    def add_chunks(self, chunks: Sequence[ChunkEmbedding]) -> None:
        if not chunks:
            return
        for chunk in chunks:
            if not chunk.embedding:
                logger.warning(
                    "skipping chunk with empty embedding chunk_id=%s",
                    chunk.chunk_id,
                )
                continue
            norm = _vector_norm(chunk.embedding)
            if norm == 0:
                logger.warning(
                    "skipping zero-norm chunk chunk_id=%s document_id=%s",
                    chunk.chunk_id,
                    chunk.document_id,
                )
                continue
            self._chunks.append(chunk)
            self._norms.append(norm)
            logger.info(
                "chunk indexed chunk_id=%s document_id=%s",
                chunk.chunk_id,
                chunk.document_id,
                extra={"run_id": "system"},
            )

    def query(self, text: str, top_k: int = 3) -> list[RetrievedChunk]:
        if not text.strip() or not self._chunks:
            return []
        query_embedding = self._embed_text(text)
        if not query_embedding:
            return []
        query_norm = _vector_norm(query_embedding)
        if query_norm == 0:
            return []
        scored: list[tuple[float, ChunkEmbedding]] = []
        for chunk, norm in zip(self._chunks, self._norms):
            score = _cosine_similarity(query_embedding, chunk.embedding) / (
                query_norm * norm
            )
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        limited = scored[: max(top_k, 0)]
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                text=chunk.text,
                metadata=dict(chunk.metadata),
                score=score,
            )
            for score, chunk in limited
        ]


_ACTIVE_STORE: RetrievalStore | None = None


def configure_retrieval_store(store: RetrievalStore) -> None:
    """Register the globally shared retrieval store instance."""
    global _ACTIVE_STORE
    _ACTIVE_STORE = store


def get_retrieval_store() -> RetrievalStore:
    """Return the configured retrieval store."""
    if _ACTIVE_STORE is None:
        msg = "retrieval store accessed before configuration"
        raise RuntimeError(msg)
    return _ACTIVE_STORE
