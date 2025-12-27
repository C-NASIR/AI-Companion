"""Redis-backed durable stores for distributed mode.

These stores keep the same *synchronous* interfaces as the filesystem stores to
minimize refactors. In distributed deployments this introduces blocking Redis
calls from the event loop; a follow-up can migrate these to async-first stores.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from redis import Redis

from ..events import Event
from ..state import RunState
from ..workflow.models import WorkflowState
from ..observability.store import TraceNotInitializedError, TraceStoreError

logger = logging.getLogger(__name__)


def _redis_from_url(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


@dataclass(frozen=True)
class RedisStoreConfig:
    url: str
    key_prefix: str = "ai:"

    def key(self, *parts: str) -> str:
        return self.key_prefix + ":".join(parts)


class RedisEventStore:
    """Durable event store backed by Redis lists + per-run INCR sequence."""

    def __init__(self, config: RedisStoreConfig):
        self._config = config
        self._redis = _redis_from_url(config.url)

    def ensure_base_dir(self) -> None:
        return None

    def _seq_key(self, run_id: str) -> str:
        return self._config.key("run", run_id, "event_seq")

    def _events_key(self, run_id: str) -> str:
        return self._config.key("run", run_id, "events")

    def append(self, event: Event | dict[str, Any]) -> Event:
        event_model = event if isinstance(event, Event) else Event.model_validate(event)
        seq = int(self._redis.incr(self._seq_key(event_model.run_id)))
        event_model.seq = seq
        payload = json.dumps(event_model.model_dump(), separators=(",", ":"))
        self._redis.rpush(self._events_key(event_model.run_id), payload)
        return event_model

    def replay(self, run_id: str) -> list[Event]:
        raw = self._redis.lrange(self._events_key(run_id), 0, -1)
        events: list[Event] = []
        for line in raw:
            if not isinstance(line, str) or not line:
                continue
            try:
                events.append(Event.model_validate(json.loads(line)))
            except Exception:
                logger.warning("skipping malformed event run_id=%s", run_id)
        return events


class RedisStateStore:
    """Persist RunState snapshots as JSON strings."""

    def __init__(self, config: RedisStoreConfig):
        self._config = config
        self._redis = _redis_from_url(config.url)

    def ensure_base_dir(self) -> None:
        return None

    def _key(self, run_id: str) -> str:
        return self._config.key("run", run_id, "state")

    def save(self, state: RunState) -> None:
        payload = json.dumps(state.model_dump(), ensure_ascii=False)
        self._redis.set(self._key(state.run_id), payload)

    def load(self, run_id: str) -> RunState | None:
        payload = self._redis.get(self._key(run_id))
        if not isinstance(payload, str) or not payload:
            return None
        try:
            return RunState.model_validate(json.loads(payload))
        except Exception:
            return None


class RedisWorkflowStore:
    """Persist WorkflowState objects as JSON strings."""

    def __init__(self, config: RedisStoreConfig):
        self._config = config
        self._redis = _redis_from_url(config.url)

    def ensure_base_dir(self) -> None:
        return None

    def _key(self, run_id: str) -> str:
        return self._config.key("run", run_id, "workflow")

    def save(self, state: WorkflowState) -> WorkflowState:
        payload = json.dumps(state.model_dump(), ensure_ascii=False)
        self._redis.set(self._key(state.run_id), payload)
        return state

    def load(self, run_id: str) -> WorkflowState | None:
        payload = self._redis.get(self._key(run_id))
        if not isinstance(payload, str) or not payload:
            return None
        try:
            return WorkflowState.model_validate(json.loads(payload))
        except Exception:
            return None

    def load_or_create(self, run_id: str) -> WorkflowState:
        existing = self.load(run_id)
        if existing:
            return existing
        state = WorkflowState(run_id=run_id)
        return self.save(state)

    def update(self, run_id: str, mutator: Callable[[WorkflowState], None]) -> WorkflowState:
        state = self.load_or_create(run_id)
        mutator(state)
        return self.save(state)


class RedisTraceStore:
    """Durable trace storage backed by a single JSON payload per run."""

    def __init__(self, config: RedisStoreConfig):
        self._config = config
        self._redis = _redis_from_url(config.url)

    def ensure_base_dir(self) -> None:
        return None

    def _key(self, run_id: str) -> str:
        return self._config.key("run", run_id, "trace")

    def _load_payload(self, run_id: str) -> dict[str, Any]:
        payload = self._redis.get(self._key(run_id))
        if not isinstance(payload, str) or not payload:
            raise TraceNotInitializedError(f"trace {run_id} not initialized")
        try:
            parsed = json.loads(payload)
        except Exception as exc:
            raise TraceStoreError(f"trace {run_id} corrupted") from exc
        if not isinstance(parsed, dict):
            raise TraceStoreError(f"trace {run_id} corrupted")
        return parsed

    def _atomic_update(self, run_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        key = self._key(run_id)
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    existing_raw = pipe.get(key)
                    existing: dict[str, Any]
                    if isinstance(existing_raw, str) and existing_raw:
                        existing = json.loads(existing_raw)
                        if not isinstance(existing, dict):
                            existing = {}
                    else:
                        existing = {}
                    updated = mutator(existing)
                    pipe.multi()
                    pipe.set(key, json.dumps(updated, ensure_ascii=False))
                    pipe.execute()
                    return updated
                except Exception as exc:
                    # Retry on watch errors.
                    if "WatchError" in exc.__class__.__name__:
                        continue
                    raise
                finally:
                    try:
                        pipe.reset()
                    except Exception:
                        pass

    @staticmethod
    def _ensure_totals(trace: dict[str, Any]) -> dict[str, Any]:
        totals = trace.get("totals") if isinstance(trace, dict) else None
        if not isinstance(totals, dict):
            totals = {}
        normalized = {
            "total_cost_usd": float(totals.get("total_cost_usd") or 0.0),
            "total_model_calls": int(totals.get("total_model_calls") or 0),
            "total_input_tokens": int(totals.get("total_input_tokens") or 0),
            "total_output_tokens": int(totals.get("total_output_tokens") or 0),
        }
        trace["totals"] = normalized
        return trace

    def init_trace(self, run_id: str, trace_payload: dict[str, Any]) -> dict[str, Any]:
        def _mut(existing: dict[str, Any]) -> dict[str, Any]:
            trace = existing.get("trace") if isinstance(existing.get("trace"), dict) else {}
            trace.update(trace_payload)
            existing["trace"] = self._ensure_totals(trace)
            spans = existing.get("spans")
            if not isinstance(spans, list):
                existing["spans"] = []
            return existing

        payload = self._atomic_update(run_id, _mut)
        return payload.get("trace") or {}

    def update_trace(self, run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        def _mut(existing: dict[str, Any]) -> dict[str, Any]:
            trace = existing.get("trace") if isinstance(existing.get("trace"), dict) else {}
            trace.update(updates)
            existing["trace"] = self._ensure_totals(trace)
            spans = existing.get("spans")
            if not isinstance(spans, list):
                existing["spans"] = []
            return existing

        payload = self._atomic_update(run_id, _mut)
        return payload.get("trace") or {}

    def append_span(self, run_id: str, span_payload: dict[str, Any]) -> dict[str, Any]:
        def _mut(existing: dict[str, Any]) -> dict[str, Any]:
            spans = existing.get("spans")
            if not isinstance(spans, list):
                spans = []
            spans.append(dict(span_payload))
            existing["spans"] = spans
            trace = existing.get("trace") if isinstance(existing.get("trace"), dict) else {}
            existing["trace"] = self._ensure_totals(trace)
            return existing

        self._atomic_update(run_id, _mut)
        return span_payload

    def update_span(self, run_id: str, span_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        def _mut(existing: dict[str, Any]) -> dict[str, Any]:
            spans = existing.get("spans")
            if not isinstance(spans, list):
                spans = []
            found = None
            for index, record in enumerate(spans):
                if isinstance(record, dict) and record.get("span_id") == span_id:
                    record.update(updates)
                    spans[index] = record
                    found = record
                    break
            if found is None:
                raise TraceStoreError(f"span {span_id} not found in trace {run_id}")
            existing["spans"] = spans
            trace = existing.get("trace") if isinstance(existing.get("trace"), dict) else {}
            existing["trace"] = self._ensure_totals(trace)
            return existing

        payload = self._atomic_update(run_id, _mut)
        spans = payload.get("spans") or []
        for record in spans:
            if isinstance(record, dict) and record.get("span_id") == span_id:
                return record
        raise TraceStoreError(f"span {span_id} not found in trace {run_id}")

    def increment_totals(
        self,
        run_id: str,
        *,
        cost_delta: float = 0.0,
        model_calls_delta: int = 0,
        input_tokens_delta: int = 0,
        output_tokens_delta: int = 0,
    ) -> dict[str, Any]:
        def _mut(existing: dict[str, Any]) -> dict[str, Any]:
            trace = existing.get("trace") if isinstance(existing.get("trace"), dict) else {}
            trace = self._ensure_totals(trace)
            totals = trace["totals"]
            totals["total_cost_usd"] = round(
                float(totals.get("total_cost_usd", 0.0)) + float(cost_delta or 0.0),
                6,
            )
            totals["total_model_calls"] = int(totals.get("total_model_calls", 0)) + max(
                int(model_calls_delta or 0), 0
            )
            totals["total_input_tokens"] = int(totals.get("total_input_tokens", 0)) + max(
                int(input_tokens_delta or 0), 0
            )
            totals["total_output_tokens"] = int(totals.get("total_output_tokens", 0)) + max(
                int(output_tokens_delta or 0), 0
            )
            trace["totals"] = totals
            existing["trace"] = trace
            spans = existing.get("spans")
            if not isinstance(spans, list):
                existing["spans"] = []
            return existing

        payload = self._atomic_update(run_id, _mut)
        trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
        trace = self._ensure_totals(trace)
        return trace.get("totals") or {}

    def load_trace(self, run_id: str) -> dict[str, Any]:
        payload = self._load_payload(run_id)
        trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
        trace = self._ensure_totals(dict(trace))
        spans = payload.get("spans") if isinstance(payload.get("spans"), list) else []
        return {"trace": trace, "spans": list(spans)}

    def load_spans(self, run_id: str) -> list[dict[str, Any]]:
        payload = self._load_payload(run_id)
        spans = payload.get("spans") if isinstance(payload.get("spans"), list) else []
        return list(spans)

