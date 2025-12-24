"""Durable trace storage for Session 8 observability."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class TraceStoreError(RuntimeError):
    """Base class for trace store errors."""


class TraceNotInitializedError(TraceStoreError):
    """Raised when a trace file is missing for the requested run."""


class TraceStore:
    """Appends and updates trace payloads atomically."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _trace_file(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.json"

    def _get_lock(self, run_id: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[run_id] = lock
            return lock

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            serialized = json.dumps(payload, separators=(",", ":"))
            handle.write(serialized)
        tmp_path.replace(path)

    def _load_payload(self, run_id: str) -> dict[str, Any]:
        path = self._trace_file(run_id)
        if not path.exists():
            msg = f"trace {run_id} not initialized"
            raise TraceNotInitializedError(msg)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def init_trace(self, run_id: str, trace_payload: dict[str, Any]) -> dict[str, Any]:
        """Create (or refresh) the trace envelope for a run."""
        path = self._trace_file(run_id)
        lock = self._get_lock(run_id)
        with lock:
            if path.exists():
                payload = self._load_payload(run_id)
                existing = payload.get("trace") or {}
                existing.update(trace_payload)
                payload["trace"] = existing
            else:
                payload = {"trace": dict(trace_payload), "spans": []}
            self._atomic_write(path, payload)
        return payload["trace"]

    def update_trace(self, run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update selected fields on the stored trace."""
        lock = self._get_lock(run_id)
        with lock:
            payload = self._load_payload(run_id)
            trace = payload.get("trace") or {}
            trace.update(updates)
            payload["trace"] = trace
            self._atomic_write(self._trace_file(run_id), payload)
        return trace

    def append_span(self, run_id: str, span_payload: dict[str, Any]) -> dict[str, Any]:
        """Append a span record to the trace file."""
        lock = self._get_lock(run_id)
        with lock:
            payload = self._load_payload(run_id)
            spans: list[dict[str, Any]] = payload.get("spans") or []
            spans.append(dict(span_payload))
            payload["spans"] = spans
            self._atomic_write(self._trace_file(run_id), payload)
        return span_payload

    def update_span(
        self,
        run_id: str,
        span_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing span in place."""
        lock = self._get_lock(run_id)
        with lock:
            payload = self._load_payload(run_id)
            spans: list[dict[str, Any]] = payload.get("spans") or []
            for index, record in enumerate(spans):
                if record.get("span_id") == span_id:
                    record.update(updates)
                    spans[index] = record
                    payload["spans"] = spans
                    self._atomic_write(self._trace_file(run_id), payload)
                    return record
        msg = f"span {span_id} not found in trace {run_id}"
        raise TraceStoreError(msg)

    def load_trace(self, run_id: str) -> dict[str, Any]:
        """Return both the trace envelope and spans."""
        lock = self._get_lock(run_id)
        with lock:
            payload = self._load_payload(run_id)
            return {
                "trace": dict(payload.get("trace") or {}),
                "spans": list(payload.get("spans") or []),
            }

    def load_spans(self, run_id: str) -> list[dict[str, Any]]:
        """Return all spans for the run."""
        lock = self._get_lock(run_id)
        with lock:
            payload = self._load_payload(run_id)
            return list(payload.get("spans") or [])
