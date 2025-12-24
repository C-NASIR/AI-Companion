"""Response node logic for the intelligence graph."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from ...model import stream_chat
from ...state import PlanType, RunPhase, RunState
from ..context import NodeContext
from ..utils import log_run


async def _stream_direct_answer(
    state: RunState,
    ctx: NodeContext,
    retrieved_chunks: Sequence[Mapping[str, Any]],
) -> None:
    first_chunk = True
    async for chunk in stream_chat(
        state.message,
        state.context,
        state.mode,
        state.run_id,
        retrieved_chunks,
    ):
        if first_chunk:
            await ctx.emit_status(state, "responding")
            first_chunk = False
        state.append_output(chunk)
        await ctx.emit_output(state, chunk)


def _chunk_text(text: str, size: int = 32) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def _stream_synthetic_response(
    state: RunState, ctx: NodeContext, template: str
) -> None:
    await ctx.emit_status(state, "responding")
    snippet = state.message.strip()[:80] or "..."
    full = template.format(
        mode=state.mode.value,
        snippet=snippet,
        run_id=state.run_id,
    )
    for chunk in _chunk_text(full):
        state.append_output(chunk)
        await ctx.emit_output(state, chunk)
        await asyncio.sleep(0.05)


async def respond_node(state: RunState, ctx: NodeContext) -> RunState:
    """Generate output based on plan."""
    async with ctx.node_scope(state, "respond", RunPhase.RESPOND):
        plan = state.plan_type or PlanType.DIRECT_ANSWER
        log_run(state.run_id, "respond strategy=%s", plan.value)
        if plan == PlanType.DIRECT_ANSWER:
            retrieved_chunks = state.retrieved_chunks
            await _stream_direct_answer(state, ctx, retrieved_chunks)
            strategy = "model_stream"
            notes = None
        elif plan == PlanType.NEEDS_CLARIFICATION:
            strategy = "clarify_static"
            notes = "requesting additional details"
            template = (
                "Mode {mode}: I need more details about \"{snippet}\" to continue. "
                "Please clarify so run {run_id} can proceed."
            )
            await _stream_synthetic_response(state, ctx, template)
        else:
            strategy = "refuse_static"
            notes = "insufficient or unsafe request"
            template = (
                "Mode {mode}: I cannot produce a reliable response for \"{snippet}\". "
                "Run {run_id} must stop here."
            )
            await _stream_synthetic_response(state, ctx, template)

        state.record_decision("response_strategy", strategy, notes=notes)
        await ctx.emit_decision(state, "response_strategy", strategy, notes)
    return state
