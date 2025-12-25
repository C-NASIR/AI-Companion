"""Knowledge ingestion pipeline for Session 5."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openai import OpenAI

from .events import EventBus, new_event
from .knowledge import set_corpus_version
from .model import OPENAI_API_KEY, OPENAI_BASE_URL
from .retrieval import ChunkEmbedding, RetrievalStore

logger = logging.getLogger(__name__)

DEFAULT_DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
KNOWLEDGE_RUN_ID = "knowledge-ingestion"
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def _extract_title(text: str, default: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or default
    return default


def _chunk_text(
    text: str, *, chunk_size: int = 500, overlap: int = 100
) -> Iterable[str]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")
    start = 0
    step = chunk_size - overlap
    text = text.strip()
    if not text:
        return []
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end == len(text):
            break
        start += step


class EmbeddingGenerator:
    """Shared embedding helper using OpenAI when available."""

    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self._use_openai = bool(OPENAI_API_KEY)

    def _client_kwargs(self) -> dict[str, str]:
        kwargs: dict[str, str] = {}
        if OPENAI_API_KEY:
            kwargs["api_key"] = OPENAI_API_KEY
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        return kwargs

    def _get_client(self) -> OpenAI:
        if not self._use_openai:
            raise RuntimeError("OpenAI embeddings unavailable")
        if self._client is None:
            self._client = OpenAI(**self._client_kwargs())
        return self._client

    @staticmethod
    def _fake_embedding(text: str, dim: int = 128) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(dim)]

    def embed(self, text: str) -> list[float]:
        if not self._use_openai:
            return self._fake_embedding(text)
        try:
            client = self._get_client()
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[text],
            )
            embedding = response.data[0].embedding
            return list(embedding)
        except Exception:
            logger.exception(
                "embedding request failed, falling back to fake embedding",
                extra={"run_id": KNOWLEDGE_RUN_ID},
            )
            self._use_openai = False
            return self._fake_embedding(text)


@dataclass
class SourceDocument:
    document_id: str
    title: str
    text: str
    source_path: str


class KnowledgeIngestion:
    """Pipeline that loads markdown documents and stores chunk embeddings."""

    def __init__(
        self,
        *,
        docs_dir: Path = DEFAULT_DOCS_DIR,
        store: RetrievalStore,
        embed_text: Callable[[str], list[float]],
        chunk_size: int = 500,
        overlap: int = 100,
    ):
        self.docs_dir = docs_dir
        self.store = store
        self.embed_text = embed_text
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _load_documents(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        if not self.docs_dir.exists():
            logger.warning(
                "docs directory missing path=%s",
                self.docs_dir,
                extra={"run_id": KNOWLEDGE_RUN_ID},
            )
            return documents
        for path in sorted(self.docs_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            document_id = path.name
            title = _extract_title(text, default=path.stem.replace("_", " ").title())
            documents.append(
                SourceDocument(
                    document_id=document_id,
                    title=title,
                    text=text,
                    source_path=str(path),
                )
            )
        return documents

    def _chunk_document(self, document: SourceDocument) -> list[ChunkEmbedding]:
        chunks: list[ChunkEmbedding] = []
        for index, chunk_text in enumerate(
            _chunk_text(document.text, chunk_size=self.chunk_size, overlap=self.overlap)
        ):
            chunk_id = f"{document.document_id}::{index:03d}"
            metadata = {
                "document_id": document.document_id,
                "title": document.title,
                "source_path": document.source_path,
                "chunk_index": index,
            }
            embedding = self.embed_text(chunk_text)
            chunks.append(
                ChunkEmbedding(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    text=chunk_text,
                    metadata=metadata,
                    embedding=embedding,
                )
            )
        return chunks

    def _compute_corpus_version(self, documents: list[SourceDocument]) -> str:
        hasher = hashlib.sha256()
        for document in documents:
            hasher.update(document.document_id.encode("utf-8"))
            hasher.update(hashlib.sha256(document.text.encode("utf-8")).digest())
        digest = hasher.hexdigest()
        return digest or "empty"

    def ingest(self) -> dict[str, int | str]:
        documents = self._load_documents()
        corpus_version = self._compute_corpus_version(documents)
        total_chunks = 0
        for document in documents:
            chunks = self._chunk_document(document)
            total_chunks += len(chunks)
            self.store.add_chunks(chunks)
            logger.info(
                "document ingested document_id=%s chunks=%s",
                document.document_id,
                len(chunks),
                extra={"run_id": KNOWLEDGE_RUN_ID},
            )
        logger.info(
            "knowledge ingestion completed documents=%s chunks=%s",
            len(documents),
            total_chunks,
            extra={"run_id": KNOWLEDGE_RUN_ID},
        )
        set_corpus_version(corpus_version)
        return {
            "documents_ingested": len(documents),
            "chunks_indexed": total_chunks,
            "corpus_version": corpus_version,
        }


async def run_ingestion(
    store: RetrievalStore,
    *,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    embedder: EmbeddingGenerator | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, int]:
    """Run ingestion on a background thread so startup remains responsive."""

    embedder = embedder or EmbeddingGenerator()
    pipeline = KnowledgeIngestion(docs_dir=docs_dir, store=store, embed_text=embedder.embed)
    if event_bus:
        await event_bus.publish(
            new_event(
                "knowledge.ingestion.started",
                KNOWLEDGE_RUN_ID,
                {"docs_dir": str(docs_dir)},
            )
        )
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, pipeline.ingest)
    if event_bus:
        await event_bus.publish(
            new_event(
                "knowledge.ingestion.completed",
                KNOWLEDGE_RUN_ID,
                stats,
            )
        )
    return stats
