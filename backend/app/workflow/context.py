"""Shared helpers for workflow activities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Mapping, Sequence

from ..events import EventBus, new_event
from ..guardrails.base import GuardrailViolation
from ..guardrails.context_sanitizer import ContextSanitizer
from ..guardrails.injection_detector import InjectionDetector
from ..guardrails.output_validator import OutputValidator
from ..guardrails.refusal import apply_refusal
from ..mcp.schema import ToolDescriptor
from ..retrieval import RetrievedChunk, RetrievalStore
from ..state import RunPhase, RunState
from ..state_store import StateStore
from ..observability.tracer import Tracer
from .exceptions import ExternalEventRequired, HumanApprovalRequired


class ActivityContext:
    """Lightweight helper for workflow activities."""

    def __init__(
        self,
        bus: EventBus,
        state_store: StateStore,
        retrieval_store: RetrievalStore,
        allowed_tools_provider: Callable[[RunState], Sequence[ToolDescriptor]]
        | None = None,
        tracer: Tracer | None = None,
        *,
        context_sanitizer: ContextSanitizer | None = None,
        output_validator: OutputValidator | None = None,
        injection_detector: InjectionDetector | None = None,
    ):
        self.bus = bus
        self.state_store = state_store
        self.retrieval_store = retrieval_store
        self._allowed_tools_provider = allowed_tools_provider
        self.tracer = tracer
        self._workflow_spans: dict[str, str] = {}
        self._node_spans: dict[str, str] = {}
        self.context_sanitizer = context_sanitizer
        self.output_validator = output_validator
        self.injection_detector = injection_detector

    async def emit_event(self, state: RunState, event_type: str, data: Mapping[str, object]) -> None:
        """Publish an event with run metadata."""
        await self.bus.publish(new_event(event_type, state.run_id, data))

    async def emit_status(self, state: RunState, value: str) -> None:
        await self.emit_event(state, "status.changed", {"value": value})

    async def emit_decision(
        self, state: RunState, name: str, value: str, notes: str | None = None
    ) -> None:
        payload: dict[str, object] = {"name": name, "value": value}
        if notes:
            payload["notes"] = notes
        await self.emit_event(state, "decision.made", payload)

    async def emit_output(self, state: RunState, text: str) -> None:
        await self.emit_event(state, "output.chunk", {"text": text})

    async def emit_error(self, state: RunState, node_name: str, message: str) -> None:
        await self.emit_event(state, "error.raised", {"node": node_name, "message": message})

    def save_state(self, state: RunState) -> None:
        """Persist the latest run snapshot."""
        self.state_store.save(state)

    def allowed_tools(self, state: RunState) -> list[ToolDescriptor]:
        """Return allowed tools for the provided state."""
        if not self._allowed_tools_provider:
            return []
        return list(self._allowed_tools_provider(state))

    def set_active_workflow_span(self, run_id: str, span_id: str | None) -> None:
        """Mark the workflow span used as parent for node spans."""
        if span_id:
            self._workflow_spans[run_id] = span_id
        else:
            self._workflow_spans.pop(run_id, None)

    def current_workflow_span(self, run_id: str) -> str | None:
        return self._workflow_spans.get(run_id)

    def current_node_span(self, run_id: str) -> str | None:
        return self._node_spans.get(run_id)

    def add_node_attribute(self, run_id: str, key: str, value: object) -> None:
        """Attach metadata to the active node span."""
        if not self.tracer:
            return
        span_id = self._node_spans.get(run_id)
        if not span_id:
            return
        self.tracer.add_span_attribute(run_id, span_id, key, value)

    @asynccontextmanager
    async def step_scope(self, state: RunState, name: str, phase: RunPhase):
        """Emit lifecycle events mirroring the previous node_scope."""
        state.transition_phase(phase)
        run_id = state.run_id
        span_id: str | None = None
        status = "success"
        error_payload: dict[str, object] | None = None
        if self.tracer:
            span_id = self.tracer.start_span(
                run_id,
                f"intelligence.{name}",
                "intelligence",
                parent_span_id=self._workflow_spans.get(run_id),
                attributes={
                    "node": name,
                    "phase": phase.value,
                    "is_evaluation": state.is_evaluation,
                },
            )
            self._node_spans[run_id] = span_id
        await self.emit_event(state, "node.started", {"name": name})
        try:
            yield
        except HumanApprovalRequired as exc:
            status = "waiting"
            error_payload = {"error_type": "approval_wait", "reason": exc.reason}
            raise
        except ExternalEventRequired as exc:
            status = "waiting"
            error_payload = {
                "error_type": "tool_wait",
                "reason": exc.reason,
                "events": list(exc.event_types),
            }
            raise
        except Exception as exc:
            status = "failed"
            error_payload = {
                "error_type": _error_type_for_phase(phase),
                "error": exc.__class__.__name__,
                "message": str(exc),
            }
            raise
        finally:
            self.save_state(state)
            await self.emit_event(state, "node.completed", {"name": name})
            if self.tracer and span_id:
                if error_payload and isinstance(error_payload, dict):
                    err_type = error_payload.get("error_type")
                    if err_type:
                        self.tracer.add_span_attribute(run_id, span_id, "error_type", err_type)
                self.tracer.end_span(run_id, span_id, status, error_payload)
            self._node_spans.pop(run_id, None)

    async def sanitize_chunks(
        self, state: RunState, chunks: Sequence[RetrievedChunk]
    ) -> Sequence[RetrievedChunk]:
        """Apply context sanitization and detection before storing chunks."""
        if not chunks:
            return chunks
        sanitized: list[RetrievedChunk] = []
        for chunk in chunks:
            text = chunk.text or ""
            if self.injection_detector:
                await self.injection_detector.scan(state.run_id, text, "retrieval")
            if not self.context_sanitizer:
                sanitized.append(chunk)
                continue
            cleaned = await self.context_sanitizer.sanitize_chunk(
                state.run_id,
                chunk.chunk_id,
                text,
            )
            if cleaned != text:
                state.record_sanitized_chunk(chunk.chunk_id)
            sanitized.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=cleaned,
                    metadata=chunk.metadata,
                    score=chunk.score,
                )
            )
        return sanitized

    async def ensure_output_safe(
        self,
        state: RunState,
        *,
        detect_injection: bool = True,
        enforce_citations: bool = True,
    ) -> None:
        """Run injection detection and output validation before streaming."""
        payload = state.output_text or ""
        if detect_injection and self.injection_detector:
            await self.injection_detector.scan(state.run_id, payload, "output")
        if not self.output_validator:
            return
        try:
            await self.output_validator.validate(
                state,
                enforce_citations=enforce_citations,
            )
        except GuardrailViolation as violation:
            state.set_guardrail_status(
                "guardrail_triggered",
                reason=violation.assessment.notes or violation.assessment.threat_type.value,
                layer=violation.layer,
                threat_type=violation.assessment.threat_type.value,
            )
            apply_refusal(state, reason=violation.assessment.threat_type.value)
            raise


def _error_type_for_phase(phase: RunPhase) -> str:
    if phase == RunPhase.PLAN:
        return "bad_plan"
    if phase == RunPhase.RETRIEVE:
        return "retrieval_failure"
    if phase == RunPhase.VERIFY:
        return "verification_failure"
    return "network_failure"
