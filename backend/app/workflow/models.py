"""Workflow state primitives for durable orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import iso_timestamp
from ..state import RunState


class WorkflowStatus(str, Enum):
    """Top-level status values persisted with every workflow transition."""

    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


WORKFLOW_STEPS: tuple[str, ...] = (
    "receive",
    "plan",
    "retrieve",
    "respond",
    "verify",
    "maybe_approve",
    "finalize",
)

INITIAL_STEP = WORKFLOW_STEPS[0]
WORKFLOW_NEXT_STEP: dict[str, str | None] = {
    current: WORKFLOW_STEPS[idx + 1] if idx + 1 < len(WORKFLOW_STEPS) else None
    for idx, current in enumerate(WORKFLOW_STEPS)
}


class WorkflowState(BaseModel):
    """Durable workflow state persisted after every transition."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    current_step: str = Field(default=INITIAL_STEP)
    status: WorkflowStatus = Field(default=WorkflowStatus.RUNNING)
    attempts: dict[str, int] = Field(default_factory=dict)
    waiting_for_human: bool = False
    human_decision: str | None = None
    last_error: dict[str, Any] | None = None
    pending_events: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=iso_timestamp)
    updated_at: str = Field(default_factory=iso_timestamp)

    def touch(self) -> None:
        """Refresh the updated_at timestamp."""
        self.updated_at = iso_timestamp()

    def record_attempt(self, step: str | None = None) -> int:
        """Increment attempt count for the provided step."""
        key = step or self.current_step
        current = self.attempts.get(key, 0) + 1
        self.attempts[key] = current
        self.touch()
        return current

    def advance_to(self, step: str) -> None:
        """Update the workflow to the provided step and clear transient flags."""
        self.current_step = step
        self.status = WorkflowStatus.RUNNING
        self.waiting_for_human = False
        self.last_error = None
        self.pending_events = []
        self.touch()

    def mark_retrying(self, error: dict[str, Any] | None = None) -> None:
        """Set retrying state and optional error payload."""
        self.status = WorkflowStatus.RETRYING
        self.last_error = dict(error) if error else None
        self.pending_events = []
        self.touch()

    def mark_waiting_for_human(self) -> None:
        """Pause workflow for human approval."""
        self.waiting_for_human = True
        self.status = WorkflowStatus.WAITING_FOR_APPROVAL
        self.pending_events = []
        self.touch()

    def set_human_decision(self, decision: str) -> None:
        """Persist human approval/denial decision."""
        self.human_decision = decision
        self.waiting_for_human = False
        self.pending_events = []
        self.touch()

    def mark_completed(self) -> None:
        """Mark workflow as completed."""
        self.status = WorkflowStatus.COMPLETED
        self.waiting_for_human = False
        self.pending_events = []
        self.touch()

    def mark_failed(self, error: dict[str, Any] | None = None) -> None:
        """Mark workflow as failed and store the error payload."""
        self.status = WorkflowStatus.FAILED
        self.waiting_for_human = False
        self.last_error = dict(error) if error else None
        self.pending_events = []
        self.touch()

    def wait_for_events(self, event_types: Sequence[str]) -> None:
        """Persist the event types required to resume."""
        self.pending_events = list(event_types)
        self.touch()

    def clear_pending_events(self) -> None:
        """Clear pending events when requirements are satisfied."""
        if self.pending_events:
            self.pending_events = []
            self.touch()


ActivityResult = tuple[RunState, WorkflowState]
ActivityFunc = Callable[[RunState, WorkflowState], Awaitable[ActivityResult]]
