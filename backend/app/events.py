"""Event backbone primitives for Session 3."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4
import threading

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schemas import iso_timestamp
from .guardrails.threats import ThreatAssessment, ThreatConfidence

logger = logging.getLogger(__name__)


class Event(BaseModel):
    """Durable event structure stored per run."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    seq: int = Field(default=0)
    ts: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class ToolRequestedPayload(BaseModel):
    """Data stored with tool.requested events."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any]
    source: str | None = None
    permission_scope: str | None = None
    parent_span_id: str | None = None


class ToolCompletedPayload(BaseModel):
    """Data stored with tool.completed events."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    output: dict[str, Any]
    duration_ms: int = Field(ge=0)


class ToolFailedPayload(BaseModel):
    """Data stored with tool.failed events."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    error: dict[str, Any]
    duration_ms: int = Field(ge=0)


class RetrievalCompletedPayload(BaseModel):
    """Data stored with retrieval.completed events."""

    model_config = ConfigDict(extra="forbid")

    number_of_chunks: int = Field(ge=0)
    chunk_ids: list[str]


class ToolDiscoveredPayload(BaseModel):
    """Data stored when tools become available for a run."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    source: str
    permission_scope: str


class ToolDeniedPayload(BaseModel):
    """Data stored for denied tool execution."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    permission_scope: str
    reason: str


class ToolServerErrorPayload(BaseModel):
    """Data stored when an MCP server raises an error."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    error: dict[str, Any]


class GuardrailTriggeredPayload(BaseModel):
    """Data stored when a guardrail prevents a harmful action."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    layer: Literal["input", "context", "output", "tool"]
    threat_type: str
    confidence: ThreatConfidence
    notes: str | None = None


class ContextSanitizedPayload(BaseModel):
    """Data stored when retrieved context is sanitized."""

    model_config = ConfigDict(extra="forbid")

    original_chunk_id: str
    sanitization_applied: bool
    notes: str | None = None


class InjectionDetectedPayload(BaseModel):
    """Signal-only prompt injection detection events."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    location: Literal["input", "retrieval", "output"]
    confidence: ThreatConfidence
    pattern: str


class CostAggregatedPayload(BaseModel):
    """Run-level cost summary."""

    model_config = ConfigDict(extra="forbid")

    total_cost_usd: float
    total_model_calls: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0, default=0)
    total_output_tokens: int = Field(ge=0, default=0)


class CacheEventPayload(BaseModel):
    """Cache hit/miss metadata."""

    model_config = ConfigDict(extra="forbid")

    cache_name: Literal["retrieval", "tool_result"]
    key: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RateLimitEventPayload(BaseModel):
    """Rate limiting metadata."""

    model_config = ConfigDict(extra="forbid")

    scope: str
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DegradedModePayload(BaseModel):
    """Signals when a run enters degraded mode."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _apply_identity(payload: dict[str, Any], identity: Mapping[str, Any] | None) -> dict[str, Any]:
    if not identity:
        return payload
    tenant = identity.get("tenant_id")
    user = identity.get("user_id")
    if tenant and "tenant_id" not in payload:
        payload["tenant_id"] = tenant
    if user and "user_id" not in payload:
        payload["user_id"] = user
    return payload


def new_event(
    event_type: str, run_id: str, data: Mapping[str, Any], identity: Mapping[str, Any] | None = None
) -> Event:
    """Create a fresh event with metadata initialized."""
    payload = _apply_identity(dict(data), identity)
    return Event(
        id=str(uuid4()),
        run_id=run_id,
        seq=0,
        ts=iso_timestamp(),
        type=event_type,
        data=payload,
    )


def tool_requested_event(
    run_id: str,
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    source: str | None = None,
    permission_scope: str | None = None,
    parent_span_id: str | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to build validated tool.requested events."""
    payload = ToolRequestedPayload(
        tool_name=tool_name,
        arguments=dict(arguments),
        source=source,
        permission_scope=permission_scope,
        parent_span_id=parent_span_id,
    ).model_dump()
    return new_event("tool.requested", run_id, payload, identity=identity)


def tool_completed_event(
    run_id: str,
    *,
    tool_name: str,
    output: Mapping[str, Any],
    duration_ms: int,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to build validated tool.completed events."""
    payload = ToolCompletedPayload(
        tool_name=tool_name,
        output=dict(output),
        duration_ms=max(int(duration_ms), 0),
    ).model_dump()
    return new_event("tool.completed", run_id, payload, identity=identity)


def tool_failed_event(
    run_id: str,
    *,
    tool_name: str,
    error: Mapping[str, Any],
    duration_ms: int,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to build validated tool.failed events."""
    payload = ToolFailedPayload(
        tool_name=tool_name,
        error=dict(error),
        duration_ms=max(int(duration_ms), 0),
    ).model_dump()
    return new_event("tool.failed", run_id, payload, identity=identity)


def tool_discovered_event(
    run_id: str,
    *,
    tool_name: str,
    source: str,
    permission_scope: str,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = ToolDiscoveredPayload(
        tool_name=tool_name,
        source=source,
        permission_scope=permission_scope,
    ).model_dump()
    return new_event("tool.discovered", run_id, payload, identity=identity)


def tool_denied_event(
    run_id: str,
    *,
    tool_name: str,
    permission_scope: str,
    reason: str,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = ToolDeniedPayload(
        tool_name=tool_name,
        permission_scope=permission_scope,
        reason=reason,
    ).model_dump()
    return new_event("tool.denied", run_id, payload, identity=identity)


def tool_server_error_event(
    run_id: str,
    *,
    server_id: str,
    error: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = ToolServerErrorPayload(
        server_id=server_id,
        error=dict(error),
    ).model_dump()
    return new_event("tool.server.error", run_id, payload, identity=identity)


def retrieval_started_event(
    run_id: str,
    query: str | None = None,
    *,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to emit retrieval.started events."""
    payload: dict[str, Any] = {}
    if query is not None:
        payload["query"] = query
    payload["query_length"] = len(query or "")
    return new_event("retrieval.started", run_id, payload, identity=identity)


def retrieval_completed_event(
    run_id: str,
    chunk_ids: Sequence[str],
    *,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to emit retrieval.completed events."""
    payload = RetrievalCompletedPayload(
        number_of_chunks=len(chunk_ids), chunk_ids=list(chunk_ids)
    ).model_dump()
    return new_event("retrieval.completed", run_id, payload, identity=identity)


def guardrail_triggered_event(
    run_id: str,
    *,
    layer: Literal["input", "context", "output", "tool"],
    assessment: ThreatAssessment,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to emit guardrail.triggered events."""
    payload = GuardrailTriggeredPayload(
        layer=layer,
        threat_type=assessment.threat_type.value,
        confidence=assessment.confidence,
        notes=assessment.notes,
    ).model_dump()
    return new_event("guardrail.triggered", run_id, payload, identity=identity)


def context_sanitized_event(
    run_id: str,
    *,
    original_chunk_id: str,
    sanitization_applied: bool,
    notes: str | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to emit context.sanitized events."""
    payload = ContextSanitizedPayload(
        original_chunk_id=original_chunk_id,
        sanitization_applied=bool(sanitization_applied),
        notes=notes,
    ).model_dump()
    return new_event("context.sanitized", run_id, payload, identity=identity)


def injection_detected_event(
    run_id: str,
    *,
    location: Literal["input", "retrieval", "output"],
    confidence: ThreatConfidence,
    pattern: str,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Helper to emit injection.detected events."""
    payload = InjectionDetectedPayload(
        location=location,
        confidence=confidence,
        pattern=pattern,
    ).model_dump()
    return new_event("injection.detected", run_id, payload, identity=identity)


def cost_aggregated_event(
    run_id: str,
    *,
    total_cost_usd: float,
    total_model_calls: int,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    """Emit cost.aggregated events for run-level accounting."""
    payload = CostAggregatedPayload(
        total_cost_usd=float(total_cost_usd),
        total_model_calls=max(int(total_model_calls), 0),
        total_input_tokens=max(int(total_input_tokens), 0),
        total_output_tokens=max(int(total_output_tokens), 0),
    ).model_dump()
    return new_event("cost.aggregated", run_id, payload, identity=identity)


def cache_hit_event(
    run_id: str,
    *,
    cache_name: Literal["retrieval", "tool_result"],
    key: str,
    metadata: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = CacheEventPayload(
        cache_name=cache_name,
        key=key,
        metadata=dict(metadata or {}),
    ).model_dump()
    return new_event("cache.hit", run_id, payload, identity=identity)


def cache_miss_event(
    run_id: str,
    *,
    cache_name: Literal["retrieval", "tool_result"],
    key: str,
    metadata: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = CacheEventPayload(
        cache_name=cache_name,
        key=key,
        metadata=dict(metadata or {}),
    ).model_dump()
    return new_event("cache.miss", run_id, payload, identity=identity)


def rate_limit_exceeded_event(
    run_id: str,
    *,
    scope: str,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = RateLimitEventPayload(
        scope=scope,
        reason=reason,
        metadata=dict(metadata or {}),
    ).model_dump()
    return new_event("rate.limit.exceeded", run_id, payload, identity=identity)


def degraded_mode_event(
    run_id: str,
    *,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> Event:
    payload = DegradedModePayload(
        reason=reason,
        metadata=dict(metadata or {}),
    ).model_dump()
    return new_event("degraded.mode.entered", run_id, payload, identity=identity)


class EventStore:
    """Append-only per-run event store backed by JSONL files."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._seq_cache: dict[str, int] = {}
        self._lock = threading.Lock()

    def _event_file(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.jsonl"

    def _load_seq_from_disk(self, run_id: str) -> int:
        path = self._event_file(run_id)
        if not path.exists():
            return 0
        last_seq = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("skipping corrupt event line run_id=%s", run_id)
                    continue
                seq = int(payload.get("seq") or 0)
                if seq > last_seq:
                    last_seq = seq
        return last_seq

    def _next_seq_locked(self, run_id: str) -> int:
        seq = self._seq_cache.get(run_id)
        if seq is None:
            seq = self._load_seq_from_disk(run_id)
        seq += 1
        self._seq_cache[run_id] = seq
        return seq

    def append(self, event: Event | Mapping[str, Any]) -> Event:
        """Assign a sequence number, persist, and return the stored event."""
        event_model = event if isinstance(event, Event) else Event.model_validate(event)
        with self._lock:
            event_model.seq = self._next_seq_locked(event_model.run_id)
            path = self._event_file(event_model.run_id)
            with path.open("a", encoding="utf-8") as handle:
                payload = json.dumps(
                    event_model.model_dump(), separators=(",", ":")
                )
                handle.write(payload)
                handle.write("\n")
        return event_model

    def replay(self, run_id: str) -> list[Event]:
        """Return all stored events for a run in sequence order."""
        path = self._event_file(run_id)
        if not path.exists():
            return []
        events: list[Event] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    events.append(Event.model_validate(payload))
                except (json.JSONDecodeError, ValidationError):
                    logger.warning(
                        "skipping malformed event run_id=%s line=%s",
                        run_id,
                        line,
                    )
        return events


EventCallback = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-memory pub/sub bus that persists through the event store first."""

    def __init__(self, store: EventStore):
        self._store = store
        self._subscribers: dict[str, set[EventCallback]] = {}
        self._global_subscribers: set[EventCallback] = set()

    async def publish(self, event: Event | Mapping[str, Any]) -> Event:
        """Persist event then fan out to live subscribers."""
        stored = self._store.append(event)
        callbacks = list(self._subscribers.get(stored.run_id, ()))
        global_callbacks = list(self._global_subscribers)
        for callback in callbacks + global_callbacks:
            try:
                await callback(stored)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "event subscriber failed run_id=%s type=%s",
                    stored.run_id,
                    stored.type,
                )
        return stored

    def subscribe(self, run_id: str, callback: EventCallback) -> Callable[[], None]:
        """Register callback for run-specific events and return unsubscribe handle."""
        subscribers = self._subscribers.setdefault(run_id, set())
        subscribers.add(callback)

        def _unsubscribe() -> None:
            current = self._subscribers.get(run_id)
            if not current:
                return
            current.discard(callback)
            if not current:
                self._subscribers.pop(run_id, None)

        return _unsubscribe

    def subscribe_all(self, callback: EventCallback) -> Callable[[], None]:
        """Register callback for all run events."""
        self._global_subscribers.add(callback)

        def _unsubscribe() -> None:
            self._global_subscribers.discard(callback)

        return _unsubscribe


def _format_sse(event: Event) -> str:
    payload = event.model_dump()
    data = json.dumps(payload, separators=(",", ":"))
    return f"event: message\ndata: {data}\n\n"


async def sse_event_stream(
    run_id: str, store: EventStore, bus: EventBus
) -> AsyncIterator[str]:
    """Async generator yielding SSE-formatted replay plus live events."""
    queue: asyncio.Queue[Event] = asyncio.Queue()

    async def _subscriber(event: Event) -> None:
        await queue.put(event)

    unsubscribe = bus.subscribe(run_id, _subscriber)
    try:
        for event in store.replay(run_id):
            yield _format_sse(event)

        while True:
            event = await queue.get()
            yield _format_sse(event)
    except asyncio.CancelledError:
        raise
    finally:
        unsubscribe()
