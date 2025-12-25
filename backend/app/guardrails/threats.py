"""Threat model definitions for Session 10 guardrails."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThreatType(str, Enum):
    """Enumerates the safety failures the guardrails can detect."""

    PROMPT_INJECTION = "prompt_injection"
    INDIRECT_PROMPT_INJECTION = "indirect_prompt_injection"
    TOOL_ABUSE = "tool_abuse"
    POLICY_VIOLATION = "policy_violation"
    UNEXPECTED_OUTPUT_SHAPE = "unexpected_output_shape"


class ThreatConfidence(str, Enum):
    """Confidence scores for a detected threat signal."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ThreatAssessment(BaseModel):
    """Structured signal emitted by guardrail components."""

    model_config = ConfigDict(extra="forbid")

    threat_type: ThreatType
    confidence: ThreatConfidence = Field(default=ThreatConfidence.MEDIUM)
    notes: str | None = None

    @field_validator("notes")
    @classmethod
    def _normalize_notes(cls, value: str | None) -> str | None:
        """Trim whitespace on human-authored notes."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def to_event_payload(self) -> dict[str, str]:
        """Convert to a serializable payload for events."""
        payload = {
            "threat_type": self.threat_type.value,
            "confidence": self.confidence.value,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload
