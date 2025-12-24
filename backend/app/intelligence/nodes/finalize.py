"""Finalize node logic for the intelligence graph."""

from __future__ import annotations

from ...state import RunPhase, RunState
from ..context import NodeContext
from ..tool_feedback import build_tool_failure_text
from ..utils import log_run


async def finalize_node(state: RunState, ctx: NodeContext) -> RunState:
    """Emit outcome and completion events."""
    async with ctx.node_scope(state, "finalize", RunPhase.FINALIZE):
        passed = bool(state.verification_passed)
        outcome = "success" if passed else "failed"
        reason = state.verification_reason if not passed else None
        if not passed and state.last_tool_status == "failed":
            failure_text = build_tool_failure_text(state)
            if failure_text and not state.output_text.strip():
                state.append_output(failure_text)
                await ctx.emit_output(state, failure_text)
        state.set_outcome(outcome, reason)
        state.record_decision("outcome", outcome, notes=reason)
        await ctx.emit_decision(state, "outcome", outcome, reason)
        payload: dict[str, object] = {"final_text": state.output_text}
        if reason:
            payload["reason"] = reason
        event_type = "run.completed" if passed else "run.failed"
        await ctx.emit(state, event_type, payload)
        await ctx.emit_status(state, "complete")
        log_run(state.run_id, "finalize outcome=%s", outcome)
    return state
