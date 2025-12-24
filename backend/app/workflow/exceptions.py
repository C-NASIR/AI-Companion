"""Workflow-specific exception types shared across modules."""

from __future__ import annotations


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
