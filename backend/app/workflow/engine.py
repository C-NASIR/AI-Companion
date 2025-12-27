"""Durable workflow engine implementation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..events import Event, EventBus, new_event
from ..guardrails.base import GuardrailViolation
from ..guardrails.refusal import apply_refusal
from ..observability.tracer import Tracer
from ..state import RunState
from ..state_store import StateStore
from .context import ActivityContext
from .models import (
    ActivityFunc,
    WORKFLOW_NEXT_STEP,
    WORKFLOW_STEPS,
    WorkflowState,
    WorkflowStatus,
)
from .retries import policy_for_step
from .store import WorkflowStore
from .exceptions import ExternalEventRequired, HumanApprovalRequired, WorkflowEngineError
from ..lease import NoopRunLease, RunLease

logger = logging.getLogger(__name__)


@dataclass
class WorkflowSignal:
    """Internal signal indicating the workflow should resume or process an event."""

    reason: str
    event: Event | None = None


@dataclass
class WorkflowRuntime:
    """In-memory bookkeeping for a running workflow."""

    run_state: RunState
    workflow_state: WorkflowState
    queue: asyncio.Queue[WorkflowSignal]
    task: asyncio.Task[None] | None
    root_span_id: str | None = None
    active_step_span_id: str | None = None


class WorkflowEngine:
    """Executes workflow steps with durable state and retry handling."""

    def __init__(
        self,
        bus: EventBus,
        workflow_store: WorkflowStore,
        state_store: StateStore,
        activities: dict[str, ActivityFunc] | None = None,
        activity_context: ActivityContext | None = None,
        tracer: Tracer | None = None,
        run_lease: RunLease | None = None,
    ):
        self.bus = bus
        self.workflow_store = workflow_store
        self.state_store = state_store
        self.activities = activities or {}
        self.activity_context = activity_context
        self.tracer = tracer
        self.run_lease = run_lease or NoopRunLease()
        self._runtimes: dict[str, WorkflowRuntime] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _lease_key(run_id: str) -> str:
        return f"workflow:{run_id}"

    def register_activity(self, step: str, func: ActivityFunc) -> None:
        """Register (or replace) the activity func for a workflow step."""
        if step not in WORKFLOW_STEPS:
            msg = f"unknown workflow step {step}"
            raise ValueError(msg)
        self.activities[step] = func

    async def start_run(self, state: RunState) -> None:
        """Start a new workflow for the provided RunState."""
        lease_key = self._lease_key(state.run_id)
        if not await self.run_lease.acquire(lease_key):
            logger.info(
                "workflow lease unavailable; skipping start",
                extra={"run_id": state.run_id},
            )
            return
        async with self._lock:
            if state.run_id in self._runtimes:
                logger.warning(
                    "workflow already active",
                    extra={"run_id": state.run_id},
                )
                return
            workflow_state = self.workflow_store.load_or_create(state.run_id)
            runtime = self._build_runtime(state, workflow_state)
            self._ensure_root_span(runtime)
            self._runtimes[state.run_id] = runtime
            self._start_runtime_task(runtime)
        logger.info(
            "workflow start queued step=%s status=%s",
            workflow_state.current_step,
            workflow_state.status.value,
            extra={"run_id": state.run_id},
        )
        await self._emit_workflow_event(
            state.run_id,
            "workflow.started",
            {
                "current_step": workflow_state.current_step,
                "status": workflow_state.status.value,
            },
        )
        await runtime.queue.put(WorkflowSignal(reason="resume"))

    async def resume_run(self, run_id: str) -> None:
        """Rehydrate a workflow runner from persisted state."""
        lease_key = self._lease_key(run_id)
        if not await self.run_lease.acquire(lease_key):
            logger.info(
                "workflow lease unavailable; skipping resume",
                extra={"run_id": run_id},
            )
            return
        async with self._lock:
            runtime = self._runtimes.get(run_id)
            if runtime:
                await runtime.queue.put(WorkflowSignal(reason="resume"))
                return
            run_state = self.state_store.load(run_id)
            workflow_state = self.workflow_store.load(run_id)
            if not run_state or not workflow_state:
                logger.warning(
                    "unable to resume workflow missing_state",
                    extra={"run_id": run_id},
                )
                return
            runtime = self._build_runtime(run_state, workflow_state)
            self._ensure_root_span(runtime)
            self._runtimes[run_id] = runtime
            self._start_runtime_task(runtime)
        logger.info(
            "workflow resume requested step=%s status=%s",
            workflow_state.current_step,
            workflow_state.status.value,
            extra={"run_id": run_id},
        )
        await runtime.queue.put(WorkflowSignal(reason="resume"))

    async def handle_event(self, event: Event) -> None:
        """Forward a persisted event to the active workflow runtime."""
        runtime = await self._ensure_runtime(event.run_id)
        if not runtime:
            return
        if event.type == "workflow.approval.recorded":
            decision = event.data.get("decision")
            if isinstance(decision, str) and decision:
                await self._apply_human_decision(runtime, decision, emit_event=False)
            await runtime.queue.put(WorkflowSignal(reason="resume"))
            return
        logger.info(
            "workflow external event received type=%s",
            event.type,
            extra={"run_id": event.run_id},
        )
        await runtime.queue.put(WorkflowSignal(reason="event", event=event))

    async def record_human_decision(self, run_id: str, decision: str) -> None:
        """Persist a human approval decision and resume the workflow."""
        runtime = await self._ensure_runtime(run_id)
        if not runtime:
            return
        await self._apply_human_decision(runtime, decision, emit_event=True)
        logger.info(
            "workflow approval recorded decision=%s",
            decision,
            extra={"run_id": run_id},
        )
        await self._emit_workflow_event(
            run_id,
            "workflow.approval.recorded",
            {"decision": decision},
        )
        await runtime.queue.put(WorkflowSignal(reason="resume"))

    async def _apply_human_decision(
        self,
        runtime: WorkflowRuntime,
        decision: str,
        *,
        emit_event: bool,
    ) -> None:
        runtime.workflow_state.set_human_decision(decision)
        runtime.workflow_state.status = WorkflowStatus.RUNNING
        self.workflow_store.save(runtime.workflow_state)
        self._end_wait_span(runtime, "success")
        if emit_event:
            await self._emit_workflow_event(
                runtime.run_state.run_id,
                "workflow.approval.recorded",
                {"decision": decision},
            )

    def _build_runtime(
        self, run_state: RunState, workflow_state: WorkflowState
    ) -> WorkflowRuntime:
        queue: asyncio.Queue[WorkflowSignal] = asyncio.Queue()
        runtime = WorkflowRuntime(
            run_state=run_state,
            workflow_state=workflow_state,
            queue=queue,
            task=None,
            root_span_id=workflow_state.root_span_id,
        )
        return runtime

    def _start_runtime_task(self, runtime: WorkflowRuntime) -> None:
        if runtime.task and not runtime.task.done():
            return
        runtime.task = asyncio.create_task(
            self._run_driver(runtime.run_state.run_id),
            name=f"workflow-{runtime.run_state.run_id}",
        )

    async def _ensure_runtime(self, run_id: str) -> WorkflowRuntime | None:
        async with self._lock:
            runtime = self._runtimes.get(run_id)
            if runtime:
                return runtime
            if not await self.run_lease.acquire(self._lease_key(run_id)):
                logger.info(
                    "workflow lease unavailable; ignoring runtime creation",
                    extra={"run_id": run_id},
                )
                return None
            run_state = self.state_store.load(run_id)
            workflow_state = self.workflow_store.load(run_id)
            if not run_state or not workflow_state:
                return None
            runtime = self._build_runtime(run_state, workflow_state)
            self._ensure_root_span(runtime)
            self._runtimes[run_id] = runtime
            self._start_runtime_task(runtime)
            await runtime.queue.put(WorkflowSignal(reason="resume"))
            return runtime

    async def _run_driver(self, run_id: str) -> None:
        runtime = self._runtimes.get(run_id)
        if not runtime:
            return
        try:
            if not await self.run_lease.refresh(self._lease_key(run_id)):
                logger.info(
                    "workflow lease lost; stopping driver",
                    extra={"run_id": run_id},
                )
                return
            await self._process_until_blocked(runtime)
            while runtime.workflow_state.status not in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
            }:
                if not await self.run_lease.refresh(self._lease_key(run_id)):
                    logger.info(
                        "workflow lease lost; stopping driver",
                        extra={"run_id": run_id},
                    )
                    return
                signal = await runtime.queue.get()
                if signal.event:
                    awaited = runtime.workflow_state.pending_events
                    if awaited and signal.event.type not in awaited:
                        continue
                    runtime.workflow_state.clear_pending_events()
                    self.workflow_store.save(runtime.workflow_state)
                    self._end_wait_span(runtime, "success")
                    logger.info(
                        "workflow resumed from external event type=%s",
                        signal.event.type,
                        extra={"run_id": run_id},
                    )
                await self._process_until_blocked(runtime)
        except Exception:  # pragma: no cover - defensive guard
            logger.exception(
                "workflow driver crashed",
                extra={"run_id": run_id},
            )
        finally:
            async with self._lock:
                self._runtimes.pop(run_id, None)
            await self.run_lease.release(self._lease_key(run_id))

    async def _process_until_blocked(self, runtime: WorkflowRuntime) -> None:
        """Run workflow steps until a pause or terminal condition."""
        while True:
            if runtime.workflow_state.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
            }:
                return
            if runtime.workflow_state.waiting_for_human:
                logger.info(
                    "workflow paused for approval step=%s",
                    runtime.workflow_state.current_step,
                    extra={"run_id": runtime.run_state.run_id},
                )
                return
            if runtime.workflow_state.pending_events:
                logger.info(
                    "workflow waiting for events step=%s events=%s",
                    runtime.workflow_state.current_step,
                    ",".join(runtime.workflow_state.pending_events),
                    extra={"run_id": runtime.run_state.run_id},
                )
                return
            current_step = runtime.workflow_state.current_step
            if current_step is None:
                runtime.workflow_state.mark_completed()
                self.workflow_store.save(runtime.workflow_state)
                await self._emit_workflow_event(
                    runtime.run_state.run_id,
                    "workflow.completed",
                    {},
                )
                self._finish_trace(runtime, "success")
                return
            activity = self.activities.get(current_step)
            if not activity:
                runtime.workflow_state.mark_failed(
                    {"error": "missing_activity", "step": current_step}
                )
                self.workflow_store.save(runtime.workflow_state)
                await self._emit_workflow_event(
                    runtime.run_state.run_id,
                    "workflow.failed",
                    {"step": current_step},
                )
                self._finish_trace(runtime, "failed")
                return
            attempt = runtime.workflow_state.record_attempt(current_step)
            self.workflow_store.save(runtime.workflow_state)
            logger.info(
                "workflow step started step=%s attempt=%s",
                current_step,
                attempt,
                extra={"run_id": runtime.run_state.run_id},
            )
            await self._emit_workflow_event(
                runtime.run_state.run_id,
                "workflow.step.started",
                {"step": current_step, "attempt": attempt},
            )
            self._start_workflow_span(runtime, current_step, attempt)
            try:
                updated_run_state, updated_workflow_state = await activity(
                    runtime.run_state, runtime.workflow_state
                )
                runtime.run_state = updated_run_state
                runtime.workflow_state = updated_workflow_state
                self.state_store.save(runtime.run_state)
                self.workflow_store.save(runtime.workflow_state)
            except GuardrailViolation as exc:
                self._end_workflow_span(
                    runtime,
                    "failed",
                    {
                        "error_type": "guardrail_failure",
                        "layer": exc.layer,
                        "threat_type": exc.assessment.threat_type.value,
                    },
                )
                await self._handle_guardrail_failure(runtime, current_step, exc)
                return
            except HumanApprovalRequired as exc:
                runtime.workflow_state.mark_waiting_for_human()
                self.workflow_store.save(runtime.workflow_state)
                self._end_workflow_span(
                    runtime,
                    "waiting",
                    {"error_type": "approval_wait", "reason": exc.reason},
                )
                self._start_wait_span(
                    runtime,
                    kind="human_approval",
                    reason=exc.reason,
                    metadata={"step": current_step},
                )
                logger.info(
                    "workflow waiting for approval step=%s reason=%s",
                    current_step,
                    exc.reason,
                    extra={"run_id": runtime.run_state.run_id},
                )
                await self._emit_workflow_event(
                    runtime.run_state.run_id,
                    "workflow.waiting_for_approval",
                    {"step": current_step, "reason": exc.reason},
                )
                return
            except ExternalEventRequired as exc:
                runtime.workflow_state.wait_for_events(exc.event_types)
                runtime.workflow_state.last_error = {
                    "error": "external_event_required",
                    "resume_events": list(exc.event_types),
                    "reason": exc.reason,
                }
                self.workflow_store.save(runtime.workflow_state)
                self._end_workflow_span(
                    runtime,
                    "waiting",
                    {
                        "error_type": "tool_wait",
                        "reason": exc.reason,
                        "events": list(exc.event_types),
                    },
                )
                self._start_wait_span(
                    runtime,
                    kind="external_event",
                    reason=exc.reason,
                    metadata={
                        "step": current_step,
                        "events": ",".join(exc.event_types),
                    },
                )
                logger.info(
                    "workflow waiting for events step=%s events=%s reason=%s",
                    current_step,
                    ",".join(exc.event_types),
                    exc.reason,
                    extra={"run_id": runtime.run_state.run_id},
                )
                await self._emit_workflow_event(
                    runtime.run_state.run_id,
                    "workflow.waiting_for_event",
                    {
                        "step": current_step,
                        "event_types": list(exc.event_types),
                        "reason": exc.reason,
                    },
                )
                return
            except Exception as exc:
                should_continue, error_payload = await self._handle_activity_failure(
                    runtime, current_step, exc
                )
                status = "retried" if should_continue else "failed"
                self._end_workflow_span(runtime, status, error_payload)
                if should_continue:
                    continue
                return

            self._end_workflow_span(runtime, "success")
            await self._emit_workflow_event(
                runtime.run_state.run_id,
                "workflow.step.completed",
                {"step": current_step, "attempt": attempt},
            )
            logger.info(
                "workflow step completed step=%s attempt=%s",
                current_step,
                attempt,
                extra={"run_id": runtime.run_state.run_id},
            )
            next_step = WORKFLOW_NEXT_STEP.get(current_step)
            if next_step:
                runtime.workflow_state.advance_to(next_step)
                self.workflow_store.save(runtime.workflow_state)
                continue
            runtime.workflow_state.mark_completed()
            self.workflow_store.save(runtime.workflow_state)
            await self._emit_workflow_event(
                runtime.run_state.run_id,
                "workflow.completed",
                {},
            )
            logger.info(
                "workflow completed",
                extra={"run_id": runtime.run_state.run_id},
            )
            self._finish_trace(runtime, "success")
            return

    async def _handle_activity_failure(
        self,
        runtime: WorkflowRuntime,
        step: str,
        exc: Exception,
    ) -> tuple[bool, dict[str, Any]]:
        """Handle retries for a failed activity.

        Returns (should_retry, error_payload).
        """
        attempt = runtime.workflow_state.attempts.get(step, 1)
        policy = policy_for_step(step)
        error_payload: dict[str, Any] = {
            "step": step,
            "attempt": attempt,
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        error_payload["error_type"] = self._error_type_for_step(step)
        if policy.allows(attempt):
            runtime.workflow_state.mark_retrying(error_payload)
            self.workflow_store.save(runtime.workflow_state)
            await self._emit_workflow_event(
                runtime.run_state.run_id,
                "workflow.retrying",
                {
                    "step": step,
                    "attempt": attempt,
                    "backoff_seconds": policy.backoff_seconds,
                },
            )
            if policy.backoff_seconds:
                await asyncio.sleep(policy.backoff_seconds)
            logger.warning(
                "workflow step retrying step=%s attempt=%s backoff=%s",
                step,
                attempt,
                policy.backoff_seconds,
                extra={"run_id": runtime.run_state.run_id},
            )
            return True, error_payload
        runtime.workflow_state.mark_failed(error_payload)
        self.workflow_store.save(runtime.workflow_state)
        await self._emit_workflow_event(
            runtime.run_state.run_id,
            "workflow.failed",
            error_payload,
        )
        logger.error(
            "workflow step failed run terminated step=%s attempts=%s",
            step,
            attempt,
            extra={"run_id": runtime.run_state.run_id},
        )
        self._finish_trace(runtime, "failed")
        return False, error_payload

    async def _handle_guardrail_failure(
        self,
        runtime: WorkflowRuntime,
        step: str,
        violation: GuardrailViolation,
    ) -> None:
        """Handle guardrail-triggered failures without retries."""
        state = runtime.run_state
        reason = violation.assessment.notes or violation.assessment.threat_type.value
        state.set_guardrail_status(
            "guardrail_triggered",
            reason=reason,
            layer=violation.layer,
            threat_type=violation.assessment.threat_type.value,
        )
        state.set_verification(passed=False, reason=reason)
        if not state.output_text.strip():
            apply_refusal(state, reason=reason)
        state.set_outcome("failed", reason)

        self.state_store.save(state)
        error_payload = {
            "error": "guardrail_triggered",
            "step": step,
            "layer": violation.layer,
            "threat_type": violation.assessment.threat_type.value,
            "notes": violation.assessment.notes,
        }
        runtime.workflow_state.mark_failed(error_payload)
        self.workflow_store.save(runtime.workflow_state)
        await self._emit_workflow_event(state.run_id, "workflow.failed", error_payload)
        await self.bus.publish(
            new_event(
                "run.failed",
                state.run_id,
                {"reason": reason, "final_text": state.output_text},
                identity={"tenant_id": state.tenant_id, "user_id": state.user_id},
            )
        )
        self._finish_trace(runtime, "failed")

    async def _emit_workflow_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        payload = dict(data)
        payload["status"] = (
            self._runtimes.get(run_id).workflow_state.status.value
            if run_id in self._runtimes
            else data.get("status")
        )
        identity = self._identity_for_run(run_id)
        await self.bus.publish(new_event(event_type, run_id, payload, identity=identity))

    def _ensure_root_span(self, runtime: WorkflowRuntime) -> None:
        if not self.tracer or runtime.root_span_id:
            return
        run_id = runtime.run_state.run_id
        span_id = self.tracer.start_span(
            run_id,
            "workflow.run",
            "workflow",
            attributes={"run_id": run_id},
        )
        runtime.root_span_id = span_id
        runtime.workflow_state.root_span_id = span_id
        self.workflow_store.save(runtime.workflow_state)
        self.tracer.set_root_span(run_id, span_id)

    def _start_workflow_span(self, runtime: WorkflowRuntime, step: str, attempt: int) -> None:
        if not self.tracer:
            return
        parent_span_id = runtime.root_span_id
        span_id = self.tracer.start_span(
            runtime.run_state.run_id,
            f"workflow.{step}",
            "workflow",
            parent_span_id=parent_span_id,
            attributes={"step": step, "attempt": attempt},
        )
        runtime.active_step_span_id = span_id
        if self.activity_context:
            self.activity_context.set_active_workflow_span(runtime.run_state.run_id, span_id)

    def _end_workflow_span(
        self,
        runtime: WorkflowRuntime,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self.activity_context:
            self.activity_context.set_active_workflow_span(runtime.run_state.run_id, None)
        span_id = runtime.active_step_span_id
        runtime.active_step_span_id = None
        if not self.tracer or not span_id:
            return
        if error and error.get("error_type"):
            self.tracer.add_span_attribute(
                runtime.run_state.run_id,
                span_id,
                "error_type",
                error["error_type"],
            )
        self.tracer.end_span(runtime.run_state.run_id, span_id, status, error)

    def _start_wait_span(
        self,
        runtime: WorkflowRuntime,
        *,
        kind: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> None:
        if not self.tracer:
            return
        if runtime.workflow_state.wait_span_id:
            return
        attributes = {"wait_kind": kind}
        attributes.update(metadata)
        span_id = self.tracer.start_span(
            runtime.run_state.run_id,
            f"workflow.wait.{kind}",
            "workflow",
            parent_span_id=runtime.root_span_id,
            attributes=attributes,
        )
        runtime.workflow_state.wait_span_id = span_id
        runtime.workflow_state.wait_kind = kind
        runtime.workflow_state.wait_reason = reason
        self.workflow_store.save(runtime.workflow_state)

    def _end_wait_span(self, runtime: WorkflowRuntime, status: str) -> None:
        span_id = runtime.workflow_state.wait_span_id
        if not span_id or not self.tracer:
            runtime.workflow_state.wait_span_id = None
            runtime.workflow_state.wait_kind = None
            runtime.workflow_state.wait_reason = None
            self.workflow_store.save(runtime.workflow_state)
            return
        error_payload = None
        if status != "success":
            error_payload = {
                "error_type": "tool_wait"
                if runtime.workflow_state.wait_kind == "external_event"
                else "approval_wait",
                "reason": runtime.workflow_state.wait_reason or "",
            }
        self.tracer.end_span(runtime.run_state.run_id, span_id, status, error_payload)
        runtime.workflow_state.wait_span_id = None
        runtime.workflow_state.wait_kind = None
        runtime.workflow_state.wait_reason = None
        self.workflow_store.save(runtime.workflow_state)

    def _finish_trace(self, runtime: WorkflowRuntime, status: str) -> None:
        if not self.tracer or not runtime.root_span_id:
            return
        run_id = runtime.run_state.run_id
        self.tracer.end_span(run_id, runtime.root_span_id, status, None)
        self.tracer.complete_trace(run_id, status)
        runtime.root_span_id = None
        runtime.workflow_state.root_span_id = None
        self.workflow_store.save(runtime.workflow_state)

    @staticmethod
    def _error_type_for_step(step: str) -> str:
        mapping = {
            "plan": "bad_plan",
            "retrieve": "retrieval_failure",
            "verify": "verification_failure",
        }
        return mapping.get(step, "network_failure")
    def _identity_for_run(self, run_id: str) -> dict[str, str] | None:
        runtime = self._runtimes.get(run_id)
        state = runtime.run_state if runtime else self.state_store.load(run_id)
        if not state:
            return None
        return {"tenant_id": state.tenant_id, "user_id": state.user_id}
