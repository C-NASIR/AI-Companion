"""Span tracing helpers for Session 8 observability."""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from ..schemas import iso_timestamp
from .store import TraceStore, TraceStoreError


@dataclass
class Span:
    """In-memory representation of a span record."""

    span_id: str
    trace_id: str
    name: str
    kind: str
    parent_span_id: str | None
    start_time: str
    end_time: str | None = None
    duration_ms: int | None = None
    status: str = "running"
    attributes: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a serializable dict for persistence."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": dict(self.attributes or {}),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Span":
        """Rehydrate a span from persisted payload."""
        return cls(
            span_id=str(payload.get("span_id")),
            trace_id=str(payload.get("trace_id")),
            parent_span_id=payload.get("parent_span_id"),
            name=str(payload.get("name")),
            kind=str(payload.get("kind")),
            start_time=str(payload.get("start_time")),
            end_time=payload.get("end_time"),
            duration_ms=payload.get("duration_ms"),
            status=str(payload.get("status") or "running"),
            attributes=dict(payload.get("attributes") or {}),
            error=payload.get("error"),
        )


class Tracer:
    """Records spans and traces with durable storage."""

    def __init__(self, store: TraceStore):
        self.store = store
        self._spans: dict[str, Span] = {}
        self._span_start_ns: dict[str, int] = {}
        self._stack: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def start_trace(self, run_id: str) -> dict[str, Any]:
        """Initialize a trace envelope for the provided run_id."""
        trace = {
            "trace_id": run_id,
            "start_time": iso_timestamp(),
            "status": "running",
            "root_span_id": None,
        }
        self.store.init_trace(run_id, trace)
        return trace

    def complete_trace(self, run_id: str, status: str) -> dict[str, Any]:
        """Mark a trace as finished with the provided status."""
        payload = {
            "status": status,
            "end_time": iso_timestamp(),
        }
        return self.store.update_trace(run_id, payload)

    def set_root_span(self, run_id: str, span_id: str) -> dict[str, Any]:
        """Record the root span identifier for a trace."""
        return self.store.update_trace(run_id, {"root_span_id": span_id})

    def start_span(
        self,
        run_id: str,
        name: str,
        kind: str,
        *,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        """Start a new span and persist the initial record."""
        span_id = str(uuid.uuid4())
        start_time = iso_timestamp()
        span = Span(
            span_id=span_id,
            trace_id=run_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            start_time=start_time,
            attributes=dict(attributes or {}),
        )
        with self._lock:
            self._spans[span_id] = span
            self._span_start_ns[span_id] = time.perf_counter_ns()
        self.store.append_span(run_id, span.to_dict())
        return span_id

    def end_span(
        self,
        run_id: str,
        span_id: str,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> Span:
        """Finalize a span with status/error and persist updates."""
        span = self._get_span(span_id, run_id)
        end_time = iso_timestamp()
        duration_ms = self._compute_duration_ms(span_id, span.start_time, end_time)
        span.end_time = end_time
        span.duration_ms = duration_ms
        span.status = status
        span.error = error
        with self._lock:
            self._spans[span_id] = span
            self._span_start_ns.pop(span_id, None)
        self.store.update_span(
            run_id,
            span_id,
            {
                "end_time": span.end_time,
                "duration_ms": span.duration_ms,
                "status": span.status,
                "error": span.error,
            },
        )
        return span

    def add_span_attribute(self, run_id: str, span_id: str, key: str, value: Any) -> Span:
        """Attach or update a span attribute."""
        span = self._get_span(span_id, run_id)
        span.attributes[key] = value
        with self._lock:
            self._spans[span_id] = span
        self.store.update_span(run_id, span_id, {"attributes": span.attributes})
        return span

    def current_span_id(self, run_id: str) -> str | None:
        """Return the ID of the currently activated span for a run, if any."""
        stack = self._stack.get(run_id)
        if not stack:
            return None
        return stack[-1]

    @contextlib.contextmanager
    def activate_span(self, run_id: str, span_id: str) -> Iterator[str]:
        """Temporarily push the provided span onto the run stack."""
        self._push_span(run_id, span_id)
        try:
            yield span_id
        finally:
            self._pop_span(run_id, span_id)

    def _push_span(self, run_id: str, span_id: str) -> None:
        with self._lock:
            stack = self._stack.setdefault(run_id, [])
            stack.append(span_id)

    def _pop_span(self, run_id: str, span_id: str) -> None:
        with self._lock:
            stack = self._stack.get(run_id)
            if not stack:
                return
            if stack and stack[-1] == span_id:
                stack.pop()
            else:  # safety: drop all trailing references if out of sync
                while stack and stack[-1] != span_id:
                    stack.pop()
                if stack and stack[-1] == span_id:
                    stack.pop()
            if not stack:
                self._stack.pop(run_id, None)

    def _get_span(self, span_id: str, run_id: str) -> Span:
        with self._lock:
            cached = self._spans.get(span_id)
        if cached:
            return cached
        # Load from store if necessary (e.g., after restart)
        spans = self.store.load_spans(run_id)
        for record in spans:
            if record.get("span_id") == span_id:
                span = Span.from_dict(record)
                with self._lock:
                    self._spans[span_id] = span
                return span
        msg = f"span {span_id} not found in trace {run_id}"
        raise TraceStoreError(msg)

    def _compute_duration_ms(self, span_id: str, start_time: str, end_time: str) -> int:
        start_ns = None
        with self._lock:
            start_ns = self._span_start_ns.get(span_id)
        if start_ns is not None:
            elapsed_ns = time.perf_counter_ns() - start_ns
            return max(int(elapsed_ns / 1_000_000), 0)
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
        delta = end_dt - start_dt
        return max(int(delta.total_seconds() * 1000), 0)
