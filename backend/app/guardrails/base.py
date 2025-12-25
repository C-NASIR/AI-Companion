"""Shared helpers for guardrail modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Protocol
from typing_extensions import Literal

from .threats import ThreatAssessment

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from ..events import Event
else:  # pragma: no cover - runtime placeholder
    Event = Any


class EventPublisher(Protocol):
    """Abstraction over the event bus used by guardrail modules."""

    async def publish(self, event: Event | Mapping[str, Any]) -> Event: ...


class GuardrailViolation(RuntimeError):
    """Raised when a guardrail prevents the workflow from continuing."""

    def __init__(
        self,
        layer: Literal["input", "context", "output", "tool"],
        assessment: ThreatAssessment,
        message: str,
    ):
        super().__init__(message)
        self.layer = layer
        self.assessment = assessment
        self.message = message
