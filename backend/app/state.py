"""Run state and decision tracking models for the intelligence layer."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .schemas import ChatMode, iso_timestamp


class DecisionRecord(BaseModel):
    """Structured entry describing a single decision made during a run."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str
    ts: str = Field(default_factory=iso_timestamp)
    notes: str | None = None


class PlanType(str, Enum):
    """High-level plan classification choices."""

    DIRECT_ANSWER = "direct_answer"
    NEEDS_CLARIFICATION = "needs_clarification"
    CANNOT_ANSWER = "cannot_answer"


class RunPhase(str, Enum):
    """Named phases for the fixed intelligence control graph."""

    INIT = "init"
    RECEIVE = "receive"
    PLAN = "plan"
    RESPOND = "respond"
    VERIFY = "verify"
    FINALIZE = "finalize"


class RunState(BaseModel):
    """Mutable run state that flows through each intelligence node."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    message: str
    context: str | None = None
    mode: ChatMode
    phase: RunPhase = Field(default=RunPhase.INIT)
    plan_type: PlanType | None = None
    verification_passed: bool | None = None
    verification_reason: str | None = None
    output_text: str = ""
    created_at: str = Field(default_factory=iso_timestamp)
    updated_at: str = Field(default_factory=iso_timestamp)
    decisions: list[DecisionRecord] = Field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        message: str,
        context: str | None,
        mode: ChatMode,
    ) -> "RunState":
        """Create a new RunState instance with synchronized timestamps."""
        ts = iso_timestamp()
        return cls(
            run_id=run_id,
            message=message,
            context=context,
            mode=mode,
            phase=RunPhase.INIT,
            created_at=ts,
            updated_at=ts,
        )

    def _touch(self) -> None:
        """Refresh updated_at timestamp."""
        self.updated_at = iso_timestamp()

    def transition_phase(self, new_phase: RunPhase) -> None:
        """Move the run into a new phase."""
        self.phase = new_phase
        self._touch()

    def append_output(self, text: str) -> None:
        """Append generated text to the accumulated output buffer."""
        if not text:
            return
        self.output_text += text
        self._touch()

    def record_decision(self, name: str, value: str, notes: str | None = None) -> None:
        """Store a decision entry and update the timestamp."""
        self.decisions.append(DecisionRecord(name=name, value=value, notes=notes))
        self._touch()

    def set_plan_type(self, plan_type: PlanType) -> None:
        """Assign the plan type for later stages."""
        self.plan_type = plan_type
        self._touch()

    def set_verification(
        self, *, passed: bool, reason: str | None = None
    ) -> None:
        """Capture verification results for the run."""
        self.verification_passed = passed
        self.verification_reason = reason
        self._touch()
