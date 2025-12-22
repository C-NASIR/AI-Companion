"""Shared Pydantic schemas and helpers for backend APIs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field, FieldValidationInfo, field_validator


class ChatMode(str, Enum):
    """Supported operation modes for the chat endpoint."""

    ANSWER = "answer"
    RESEARCH = "research"
    SUMMARIZE = "summarize"


class ChatRequest(BaseModel):
    """Structured request body for POST /chat."""

    message: str = Field(..., min_length=1, description="Primary user intent")
    context: str | None = Field(
        default=None, description="Optional supporting information"
    )
    mode: ChatMode = Field(default=ChatMode.ANSWER, description="Execution mode")


EventType = Literal["status", "step", "output", "error", "done", "node", "decision"]


def iso_timestamp() -> str:
    """Return an ISO-8601 timestamp string (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def build_event(event_type: EventType, run_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Construct a typed event payload."""
    return {"type": event_type, "run_id": run_id, "ts": iso_timestamp(), "data": dict(data)}


def serialize_event(event: Mapping[str, Any]) -> str:
    """Serialize an event dict as NDJSON line with compact separators."""
    return json.dumps(event, separators=(",", ":")) + "\n"


class FeedbackScore(str, Enum):
    """Feedback score provided by the user."""

    UP = "up"
    DOWN = "down"


class FeedbackRequest(BaseModel):
    """Request body for POST /feedback."""

    run_id: str = Field(..., min_length=1)
    score: FeedbackScore
    reason: str | None = Field(default=None, description="Present when score=down")
    final_text: str = Field(..., description="Full response text at completion")
    message: str = Field(..., description="Original user message")
    context: str | None = None
    mode: ChatMode

    @field_validator("reason")
    @classmethod
    def ensure_reason_when_down(
        cls, value: str | None, info: FieldValidationInfo
    ) -> str | None:
        score = info.data.get("score")
        if score == FeedbackScore.DOWN and not (value and value.strip()):
            raise ValueError("reason is required when score=down")
        return value
