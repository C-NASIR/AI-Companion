"""Planning heuristics used by the workflow runtime.

This module intentionally contains only the planner decision logic that is shared
by the workflow execution path.
"""

from __future__ import annotations

from .schemas import ChatMode
from .state import PlanType, RunState


def choose_plan(state: RunState) -> tuple[PlanType, str]:
    """Choose the plan type for a run.

    This is a simple heuristic copied from the legacy planner.
    """

    message = state.message.strip()
    if not message:
        return (PlanType.CANNOT_ANSWER, "empty message")
    if len(message) < 6:
        return (PlanType.NEEDS_CLARIFICATION, "very short message")
    lowered = message.lower()
    if state.mode == ChatMode.RESEARCH and not state.context:
        if len(message) < 18:
            return (PlanType.NEEDS_CLARIFICATION, "research mode without context")
        if any(
            marker in lowered
            for marker in (
                "research this",
                "research this idea",
                "this idea",
                "this for me",
            )
        ):
            return (PlanType.NEEDS_CLARIFICATION, "research request too underspecified")
    if any(keyword in lowered for keyword in ("confidential", "secret", "leak")):
        return (PlanType.CANNOT_ANSWER, "confidential request")
    if any(keyword in lowered for keyword in ("weather", "stock tips", "real-time stock", "real time stock")):
        return (PlanType.CANNOT_ANSWER, "real-time information request")
    if any(keyword in lowered for keyword in ("illegal", "forbidden", "unsafe")):
        return (PlanType.CANNOT_ANSWER, "potentially unsafe request")
    if message.endswith("?"):
        return (PlanType.DIRECT_ANSWER, "question detected")
    return (PlanType.DIRECT_ANSWER, "default direct answer path")


__all__ = ["choose_plan"]
