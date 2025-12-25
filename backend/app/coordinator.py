"""Workflow-aware run coordinator bridging events and the engine."""

from __future__ import annotations

import logging
from typing import Mapping

from .events import Event, EventBus, new_event
from .guardrails.base import GuardrailViolation
from .guardrails.input_gate import InputGate
from .guardrails.injection_detector import InjectionDetector
from .guardrails.refusal import apply_refusal
from .state import RunState
from .state_store import StateStore
from .observability.tracer import Tracer
from .workflow import ActivityContext, WorkflowEngine

logger = logging.getLogger(__name__)


class RunCoordinator:
    """Bridges API requests, tool events, and the workflow engine."""

    def __init__(
        self,
        bus: EventBus,
        state_store: StateStore,
        workflow_engine: WorkflowEngine,
        activity_ctx: ActivityContext,
        tracer: Tracer | None = None,
        *,
        input_gate: InputGate | None = None,
        injection_detector: InjectionDetector | None = None,
    ):
        self.bus = bus
        self.state_store = state_store
        self.workflow_engine = workflow_engine
        self.activity_ctx = activity_ctx
        self.tracer = tracer
        self.input_gate = input_gate
        self.injection_detector = injection_detector
        self._unsubscribe = self.bus.subscribe_all(self._handle_event)

    async def start_run(self, state: RunState) -> None:
        """Persist initial state, emit run.started, and delegate to workflow engine."""
        run_id = state.run_id
        if self.tracer:
            self.tracer.start_trace(run_id)
        try:
            if self.injection_detector:
                await self.injection_detector.scan(run_id, state.message, "input")
            if self.input_gate:
                await self.input_gate.enforce(run_id, state.message, state.mode)
        except GuardrailViolation as violation:
            await self._handle_guardrail_refusal(state, violation)
            return
        self.state_store.save(state)
        await self.bus.publish(
            new_event(
                "run.started",
                run_id,
                {
                    "message": state.message,
                    "context": state.context,
                    "mode": state.mode.value,
                },
            )
        )
        await self.workflow_engine.start_run(state)
        logger.info("workflow queued", extra={"run_id": run_id})

    async def shutdown(self) -> None:
        """Cleanup subscriptions."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    async def _handle_event(self, event: Event) -> None:
        if event.type in {"tool.completed", "tool.failed", "tool.denied"}:
            await self._handle_tool_event(event)

    async def _handle_tool_event(self, event: Event) -> None:
        run_id = event.run_id
        state = self.state_store.load(run_id)
        if not state:
            logger.warning("received tool event for unknown run", extra={"run_id": run_id})
            return
        tool_name = event.data.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            tool_name = "unknown"
        duration_ms = int(event.data.get("duration_ms") or 0)
        if event.type == "tool.completed":
            payload = self._coerce_mapping(event.data.get("output"))
            state.record_tool_result(
                name=tool_name,
                status="completed",
                payload=payload,
                duration_ms=duration_ms,
            )
            notes = f"{tool_name} completed"
            state.record_decision("tool_result", "completed", notes=notes)
            await self.activity_ctx.emit_decision(state, "tool_result", "completed", notes)
            logger.info(
                "tool completed recorded tool=%s duration_ms=%s",
                tool_name,
                duration_ms,
                extra={"run_id": run_id},
            )
        elif event.type == "tool.failed":
            error = self._coerce_mapping(event.data.get("error"), default={"error": "unknown"})
            state.record_tool_result(
                name=tool_name,
                status="failed",
                payload=error,
                duration_ms=duration_ms,
            )
            reason = error.get("error")
            reason_str = reason if isinstance(reason, str) and reason else "tool_failed"
            state.record_decision("tool_result", "failed", notes=reason_str)
            await self.activity_ctx.emit_decision(state, "tool_result", "failed", reason_str)
            logger.warning(
                "tool failed tool=%s reason=%s",
                tool_name,
                reason_str,
                extra={"run_id": run_id},
            )
        else:  # tool.denied
            reason = event.data.get("reason")
            if not isinstance(reason, str) or not reason:
                reason = "permission_denied"
            state.set_tool_denied(reason)
            state.record_decision("tool_result", "denied", notes=reason)
            await self.activity_ctx.emit_decision(state, "tool_result", "denied", reason)
            state.record_tool_result(
                name=tool_name,
                status="failed",
                payload={"error": reason},
                duration_ms=0,
            )
            logger.warning(
                "tool denied tool=%s reason=%s",
                tool_name,
                reason,
                extra={"run_id": run_id},
            )
        self.activity_ctx.save_state(state)
        await self.workflow_engine.handle_event(event)

    async def _handle_guardrail_refusal(
        self,
        state: RunState,
        violation: GuardrailViolation,
    ) -> None:
        """Handle guardrail-triggered refusals before workflow start."""
        run_id = state.run_id
        reason = violation.assessment.notes or violation.assessment.threat_type.value
        state.set_guardrail_status(
            "refused",
            reason=reason,
            layer=violation.layer,
            threat_type=violation.assessment.threat_type.value,
        )
        apply_refusal(state, reason=reason)
        self.state_store.save(state)
        await self.bus.publish(
            new_event(
                "run.failed",
                run_id,
                {"reason": reason, "final_text": state.output_text},
            )
        )
        if self.tracer:
            self.tracer.complete_trace(run_id, "failed")
        logger.warning(
            "run refused by guardrail layer=%s reason=%s",
            violation.layer,
            reason,
            extra={"run_id": run_id},
        )

    @staticmethod
    def _coerce_mapping(value: object, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return value
        return default or {}
