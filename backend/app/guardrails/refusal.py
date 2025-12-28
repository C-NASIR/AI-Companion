"""Standardized refusal helpers."""

from __future__ import annotations

from ..state import RunState

REFUSAL_TEXT = "This request cannot be completed as stated."


def build_refusal_message(additional_context: str | None = None) -> str:
    """Return the canonical refusal message with optional context."""
    if additional_context:
        context = additional_context.strip()
        if context:
            return f"{REFUSAL_TEXT} {context}"
    return REFUSAL_TEXT


def apply_refusal(state: RunState, reason: str | None = None) -> str:
    """Update run state to reflect a refusal."""
    message = build_refusal_message()
    state.output_text = message
    refusal_reason = reason or "guardrail_refused"
    state.record_decision("guardrail_refusal", "triggered", notes=refusal_reason)
    return message
