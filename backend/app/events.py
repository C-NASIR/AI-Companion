"""Event backbone primitives for Session 3."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4
import threading

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schemas import iso_timestamp

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


def new_event(event_type: str, run_id: str, data: Mapping[str, Any]) -> Event:
    """Create a fresh event with metadata initialized."""
    return Event(
        id=str(uuid4()),
        run_id=run_id,
        seq=0,
        ts=iso_timestamp(),
        type=event_type,
        data=dict(data),
    )


def tool_requested_event(
    run_id: str, *, tool_name: str, arguments: Mapping[str, Any]
) -> Event:
    """Helper to build validated tool.requested events."""
    payload = ToolRequestedPayload(
        tool_name=tool_name, arguments=dict(arguments)
    ).model_dump()
    return new_event("tool.requested", run_id, payload)


def tool_completed_event(
    run_id: str,
    *,
    tool_name: str,
    output: Mapping[str, Any],
    duration_ms: int,
) -> Event:
    """Helper to build validated tool.completed events."""
    payload = ToolCompletedPayload(
        tool_name=tool_name,
        output=dict(output),
        duration_ms=max(int(duration_ms), 0),
    ).model_dump()
    return new_event("tool.completed", run_id, payload)


def tool_failed_event(
    run_id: str,
    *,
    tool_name: str,
    error: Mapping[str, Any],
    duration_ms: int,
) -> Event:
    """Helper to build validated tool.failed events."""
    payload = ToolFailedPayload(
        tool_name=tool_name,
        error=dict(error),
        duration_ms=max(int(duration_ms), 0),
    ).model_dump()
    return new_event("tool.failed", run_id, payload)


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
