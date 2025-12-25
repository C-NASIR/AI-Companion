"""In-memory cache store supporting retrieval and tool results."""

from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

from ..retrieval import RetrievedChunk


def _stable_json(value: object) -> str:
    def _default(obj: object) -> object:
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, Mapping):
            return dict(obj)
        if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
            return list(obj)
        return str(obj)

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_default)


def _hash_payload(*parts: str) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
    return hasher.hexdigest()


def _chunk_to_dict(chunk: RetrievedChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "text": chunk.text,
        "metadata": deepcopy(chunk.metadata),
        "score": chunk.score,
    }


def _chunk_from_dict(payload: Mapping[str, Any]) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(payload.get("chunk_id")),
        document_id=str(payload.get("document_id")),
        text=str(payload.get("text") or ""),
        metadata=dict(payload.get("metadata") or {}),
        score=float(payload.get("score") or 0.0),
    )


class CacheStore:
    """Thread-safe cache store."""

    def __init__(self) -> None:
        self._retrieval: dict[str, list[dict[str, Any]]] = {}
        self._tool_results: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def retrieval_lookup(self, tenant_id: str, query: str, corpus_version: str, top_k: int) -> tuple[str, list[RetrievedChunk] | None]:
        key = _hash_payload(
            "retrieval",
            tenant_id or "default",
            corpus_version or "unknown",
            str(top_k),
            query or "",
        )
        with self._lock:
            cached = self._retrieval.get(key)
        if not cached:
            return key, None
        return key, [_chunk_from_dict(entry) for entry in cached]

    def store_retrieval(
        self,
        tenant_id: str,
        query: str,
        corpus_version: str,
        top_k: int,
        chunks: Sequence[RetrievedChunk],
    ) -> str:
        key = _hash_payload(
            "retrieval",
            tenant_id or "default",
            corpus_version or "unknown",
            str(top_k),
            query or "",
        )
        serialized = [_chunk_to_dict(chunk) for chunk in chunks]
        with self._lock:
            self._retrieval[key] = serialized
        return key

    def clear_retrieval(self) -> None:
        with self._lock:
            self._retrieval.clear()

    def tool_lookup(self, tenant_id: str, tool_name: str, arguments: Mapping[str, object]) -> tuple[str, dict[str, Any] | None]:
        key = _hash_payload("tool", tenant_id or "default", tool_name or "unknown", _stable_json(arguments))
        with self._lock:
            cached = self._tool_results.get(key)
        if not cached:
            return key, None
        return key, deepcopy(cached)

    def store_tool(self, tenant_id: str, tool_name: str, arguments: Mapping[str, object], output: Mapping[str, object]) -> str:
        key = _hash_payload("tool", tenant_id or "default", tool_name or "unknown", _stable_json(arguments))
        with self._lock:
            self._tool_results[key] = deepcopy(dict(output))
        return key

    def clear_tools(self) -> None:
        with self._lock:
            self._tool_results.clear()
