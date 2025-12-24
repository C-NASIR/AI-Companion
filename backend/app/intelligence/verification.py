"""Verification node logic for the intelligence graph."""

from __future__ import annotations

import re

from ..state import PlanType, RunPhase, RunState
from .context import NodeContext
from .tool_feedback import append_tool_summary_if_needed
from .utils import log_run

_CITATION_PATTERN = re.compile(r"\[([\w\-\.:]+)\]")


def _extract_cited_chunk_ids(text: str) -> list[str]:
    if not text:
        return []
    return _CITATION_PATTERN.findall(text)


def _evaluate_grounding_requirements(state: RunState) -> tuple[bool, str | None]:
    retrieved = state.retrieved_chunks
    if not retrieved:
        return True, None
    citations = _extract_cited_chunk_ids(state.output_text)
    if not citations:
        return False, "missing_citations"
    valid_ids = {chunk.chunk_id for chunk in retrieved}
    invalid = [citation for citation in citations if citation not in valid_ids]
    if invalid:
        return False, "invalid_citation"
    return True, None


def _evaluate_general_verification(state: RunState) -> tuple[bool, str | None]:
    if state.last_tool_status == "completed":
        return True, None
    if state.last_tool_status == "failed":
        return False, "tool_failed"
    text = state.output_text.strip()
    if not text:
        return False, "empty_output"
    if state.plan_type == PlanType.DIRECT_ANSWER and text.lower().startswith(
        ("i don't know", "cannot", "can't")
    ):
        return False, "low_confidence_or_refusal"
    return True, None


async def verify_node(state: RunState, ctx: NodeContext) -> RunState:
    """Perform lightweight verification."""
    async with ctx.node_scope(state, "verify", RunPhase.VERIFY):
        await append_tool_summary_if_needed(state, ctx)
        grounding_passed, grounding_reason = _evaluate_grounding_requirements(state)
        grounding_value = "pass" if grounding_passed else "fail"
        state.record_decision("grounding", grounding_value, notes=grounding_reason)
        await ctx.emit_decision(state, "grounding", grounding_value, grounding_reason)
        if grounding_passed:
            passed, reason = _evaluate_general_verification(state)
        else:
            passed, reason = False, grounding_reason
        state.set_verification(passed=passed, reason=reason)
        decision_value = "pass" if passed else "fail"
        state.record_decision("verification", decision_value, notes=reason)
        await ctx.emit_decision(state, "verification", decision_value, reason)
        log_run(state.run_id, "verification result=%s", decision_value)
    return state
