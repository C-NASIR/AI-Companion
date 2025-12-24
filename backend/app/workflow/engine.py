"""Durable workflow engine implementation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..events import Event, EventBus, new_event
from ..state import RunState
from ..state_store import StateStore
from .models import (
    ActivityFunc,
    WORKFLOW_NEXT_STEP,
    WORKFLOW_STEPS,
    WorkflowState,
    WorkflowStatus,
)
from .retries import policy_for_step
from .store import WorkflowStore

logger = logging.getLogger(__name__)


class WorkflowEngineError(Exception):
    """Base class for workflow-related failures."""


class HumanApprovalRequired(WorkflowEngineError):
    """Raised by activities to pause for human approval."""

    def __init__(self, reason: str | None = None):
        self.reason = reason or "approval_required"
        super().__init__(self.reason)


class ExternalEventRequired(WorkflowEngineError):
    """Raised by activities to pause until specific events arrive."""

    def __init__(self, event_types: tuple[str, ...], reason: str | None = None):
        if not event_types:
            msg = "ExternalEventRequired requires at least one event type"
            raise ValueError(msg)
        self.event_types = event_types
        self.reason = reason or "external_event_required"
        super().__init__(self.reason)


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


class WorkflowEngine:
    """Executes workflow steps with durable state and retry handling."""

    def __init__(
        self,
        bus: EventBus,
        workflow_store: WorkflowStore,
        state_store: StateStore,
        activities: dict[str, ActivityFunc] | None = None,
    ):
        self.bus = bus
        self.workflow_store = workflow_store
        self.state_store = state_store
        self.activities = activities or {}
        self._runtimes: dict[str, WorkflowRuntime] = {}
        self._lock = asyncio.Lock()

    def register_activity(self, step: str, func: ActivityFunc) -> None:
        """Register (or replace) the activity func for a workflow step."""
        if step not in WORKFLOW_STEPS:
            msg = f"unknown workflow step {step}"
            raise ValueError(msg)
        self.activities[step] = func

    async def start_run(self, state: RunState) -> None:
        """Start a new workflow for the provided RunState."""
        async with self._lock:
            if state.run_id in self._runtimes:
                logger.warning(
                    "workflow already active",
                    extra={"run_id": state.run_id},
                )
                return
            workflow_state = self.workflow_store.load_or_create(state.run_id)
            runtime = self._build_runtime(state, workflow_state)
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
        runtime.workflow_state.set_human_decision(decision)
        runtime.workflow_state.status = WorkflowStatus.RUNNING
        self.workflow_store.save(runtime.workflow_state)
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

    def _build_runtime(
        self, run_state: RunState, workflow_state: WorkflowState
    ) -> WorkflowRuntime:
        queue: asyncio.Queue[WorkflowSignal] = asyncio.Queue()
        runtime = WorkflowRuntime(
            run_state=run_state,
            workflow_state=workflow_state,
            queue=queue,
            task=None,
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
            run_state = self.state_store.load(run_id)
            workflow_state = self.workflow_store.load(run_id)
            if not run_state or not workflow_state:
                return None
            runtime = self._build_runtime(run_state, workflow_state)
            self._runtimes[run_id] = runtime
            self._start_runtime_task(runtime)
            await runtime.queue.put(WorkflowSignal(reason="resume"))
            return runtime

    async def _run_driver(self, run_id: str) -> None:
        runtime = self._runtimes.get(run_id)
        if not runtime:
            return
        try:
            await self._process_until_blocked(runtime)
            while runtime.workflow_state.status not in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
            }:
                signal = await runtime.queue.get()
                if signal.event:
                    awaited = runtime.workflow_state.pending_events
                    if awaited and signal.event.type not in awaited:
                        continue
                    runtime.workflow_state.clear_pending_events()
                    self.workflow_store.save(runtime.workflow_state)
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
            try:
                updated_run_state, updated_workflow_state = await activity(
                    runtime.run_state, runtime.workflow_state
                )
                runtime.run_state = updated_run_state
                runtime.workflow_state = updated_workflow_state
                self.state_store.save(runtime.run_state)
                self.workflow_store.save(runtime.workflow_state)
            except HumanApprovalRequired as exc:
                runtime.workflow_state.mark_waiting_for_human()
                self.workflow_store.save(runtime.workflow_state)
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
                should_continue = await self._handle_activity_failure(
                    runtime, current_step, exc
                )
                if should_continue:
                    continue
                return

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
            return

    async def _handle_activity_failure(
        self,
        runtime: WorkflowRuntime,
        step: str,
        exc: Exception,
    ) -> bool:
        """Handle retries for a failed activity.

        Returns True if the engine should retry the step, False if the workflow is terminal.
        """
        attempt = runtime.workflow_state.attempts.get(step, 1)
        policy = policy_for_step(step)
        error_payload: dict[str, Any] = {
            "step": step,
            "attempt": attempt,
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
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
            return True
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
        return False

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
        await self.bus.publish(new_event(event_type, run_id, payload))
