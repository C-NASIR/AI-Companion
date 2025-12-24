"""Trajectory extraction utilities for evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..events import Event, EventStore
from ..observability.store import TraceStore, TraceStoreError
from ..state import RunState
from ..state_store import StateStore


@dataclass
class StatusEvent:
    """Status change emitted during a run."""

    value: str
    ts: str


@dataclass
class DecisionEvent:
    """Recorded decision entry from the event log."""

    name: str
    value: str
    notes: str | None
    ts: str


@dataclass
class NodeEvent:
    """Node lifecycle derived from node.started/node.completed events."""

    name: str
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class RetrievalAttempt:
    """Structured representation of retrieval activity."""

    started_at: str | None
    completed_at: str | None
    query: str | None
    query_length: int | None
    chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ToolCall:
    """Tool invocation as reconstructed from tool.* events."""

    name: str
    status: str
    requested_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    source: str | None = None
    permission_scope: str | None = None


@dataclass
class Trajectory:
    """Aggregate view of a run used for evaluation scoring."""

    run_id: str
    case_id: str | None
    state: RunState
    events: list[Event]
    node_events: list[NodeEvent]
    status_history: list[StatusEvent]
    decisions: list[DecisionEvent]
    retrievals: list[RetrievalAttempt]
    tool_calls: list[ToolCall]
    trace: dict[str, Any] | None
    span_count: int
    trace_path: str | None = None


class TrajectoryExtractor:
    """Builds structured trajectories by replaying stored events."""

    def __init__(
        self,
        state_store: StateStore,
        event_store: EventStore,
        trace_store: TraceStore | None = None,
    ):
        self._state_store = state_store
        self._event_store = event_store
        self._trace_store = trace_store

    def extract(self, run_id: str, *, case_id: str | None = None) -> Trajectory:
        """Return a Trajectory for the provided run_id."""
        state = self._state_store.load(run_id)
        if not state:
            raise ValueError(f"run state not found for {run_id}")
        events = self._event_store.replay(run_id)
        node_events = self._build_node_events(events)
        status_history = self._build_status_history(events)
        decisions = self._build_decisions(events)
        retrievals = self._build_retrievals(events)
        tool_calls = self._build_tool_calls(events)
        trace_payload, span_count, trace_path = self._load_trace(run_id)
        return Trajectory(
            run_id=run_id,
            case_id=case_id,
            state=state,
            events=events,
            node_events=node_events,
            status_history=status_history,
            decisions=decisions,
            retrievals=retrievals,
            tool_calls=tool_calls,
            trace=trace_payload,
            span_count=span_count,
            trace_path=trace_path,
        )

    def _build_node_events(self, events: Sequence[Event]) -> list[NodeEvent]:
        records: list[NodeEvent] = []
        for event in events:
            if event.type == "node.started":
                name = str(event.data.get("name") or "unknown")
                records.append(NodeEvent(name=name, started_at=event.ts))
            elif event.type == "node.completed":
                name = str(event.data.get("name") or "unknown")
                for record in reversed(records):
                    if record.name == name and record.completed_at is None:
                        record.completed_at = event.ts
                        break
        return records

    def _build_status_history(self, events: Sequence[Event]) -> list[StatusEvent]:
        history: list[StatusEvent] = []
        for event in events:
            if event.type != "status.changed":
                continue
            value = str(event.data.get("value") or "")
            history.append(StatusEvent(value=value, ts=event.ts))
        return history

    def _build_decisions(self, events: Sequence[Event]) -> list[DecisionEvent]:
        decisions: list[DecisionEvent] = []
        for event in events:
            if event.type != "decision.made":
                continue
            name = str(event.data.get("name") or "")
            value = str(event.data.get("value") or "")
            notes = event.data.get("notes")
            if notes is not None:
                notes = str(notes)
            decisions.append(DecisionEvent(name=name, value=value, notes=notes, ts=event.ts))
        return decisions

    def _build_retrievals(self, events: Sequence[Event]) -> list[RetrievalAttempt]:
        attempts: list[RetrievalAttempt] = []
        for event in events:
            if event.type == "retrieval.started":
                query = event.data.get("query")
                query_length = event.data.get("query_length")
                if isinstance(query_length, str):
                    try:
                        query_length = int(query_length)
                    except ValueError:
                        query_length = None
                attempts.append(
                    RetrievalAttempt(
                        started_at=event.ts,
                        completed_at=None,
                        query=str(query) if query is not None else None,
                        query_length=int(query_length) if isinstance(query_length, int) else query_length,
                    )
                )
            elif event.type == "retrieval.completed":
                chunk_ids = event.data.get("chunk_ids") or []
                if not attempts:
                    attempts.append(
                        RetrievalAttempt(
                            started_at=None,
                            completed_at=event.ts,
                            query=None,
                            query_length=None,
                        )
                    )
                attempts[-1].completed_at = event.ts
                attempts[-1].chunk_ids = [str(chunk_id) for chunk_id in chunk_ids]
        return attempts

    def _build_tool_calls(self, events: Sequence[Event]) -> list[ToolCall]:
        calls: list[ToolCall] = []

        def _latest_pending(tool_name: str) -> ToolCall | None:
            for record in reversed(calls):
                if record.name == tool_name and record.completed_at is None:
                    return record
            return None

        for event in events:
            if event.type == "tool.requested":
                tool_name = str(event.data.get("tool_name") or "unknown")
                arguments = event.data.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                calls.append(
                    ToolCall(
                        name=tool_name,
                        status="requested",
                        requested_at=event.ts,
                        arguments=dict(arguments),
                        source=event.data.get("source"),
                        permission_scope=event.data.get("permission_scope"),
                    )
                )
            elif event.type == "tool.completed":
                tool_name = str(event.data.get("tool_name") or "unknown")
                record = _latest_pending(tool_name)
                if record is None:
                    record = ToolCall(
                        name=tool_name,
                        status="requested",
                        requested_at=event.ts,
                    )
                    calls.append(record)
                record.status = "completed"
                record.completed_at = event.ts
                duration = event.data.get("duration_ms")
                if isinstance(duration, str):
                    try:
                        duration = int(duration)
                    except ValueError:
                        duration = None
                record.duration_ms = duration
                output = event.data.get("output")
                record.output = dict(output) if isinstance(output, dict) else None
            elif event.type == "tool.failed":
                tool_name = str(event.data.get("tool_name") or "unknown")
                record = _latest_pending(tool_name)
                if record is None:
                    record = ToolCall(
                        name=tool_name,
                        status="requested",
                        requested_at=event.ts,
                    )
                    calls.append(record)
                record.status = "failed"
                record.completed_at = event.ts
                duration = event.data.get("duration_ms")
                if isinstance(duration, str):
                    try:
                        duration = int(duration)
                    except ValueError:
                        duration = None
                record.duration_ms = duration
                error = event.data.get("error")
                record.error = dict(error) if isinstance(error, dict) else None
            elif event.type == "tool.denied":
                tool_name = str(event.data.get("tool_name") or "unknown")
                record = _latest_pending(tool_name)
                if record is None:
                    record = ToolCall(
                        name=tool_name,
                        status="requested",
                        requested_at=event.ts,
                    )
                    calls.append(record)
                record.status = "denied"
                record.completed_at = event.ts
                reason = event.data.get("reason")
                record.error = {"error": "permission_denied", "reason": reason}
        return calls

    def _load_trace(self, run_id: str) -> tuple[dict[str, Any] | None, int, str | None]:
        if not self._trace_store:
            return None, 0, None
        try:
            payload = self._trace_store.load_trace(run_id)
        except TraceStoreError:
            return None, 0, None
        trace = payload.get("trace") or {}
        spans = payload.get("spans") or []
        path = str(self._trace_store.base_dir / f"{run_id}.json")
        return trace, len(spans), path


__all__ = [
    "DecisionEvent",
    "NodeEvent",
    "RetrievalAttempt",
    "StatusEvent",
    "ToolCall",
    "Trajectory",
    "TrajectoryExtractor",
]
