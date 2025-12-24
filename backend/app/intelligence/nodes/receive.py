"""Receive node for the intelligence graph."""

from __future__ import annotations

from ...state import RunPhase, RunState
from ..context import NodeContext
from ..utils import log_run


async def receive_node(state: RunState, ctx: NodeContext) -> RunState:
    """Capture the request intent."""
    async with ctx.node_scope(state, "receive", RunPhase.RECEIVE):
        log_run(
            state.run_id,
            "node receive message_length=%s context_length=%s mode=%s",
            len(state.message),
            len(state.context or ""),
            state.mode.value,
        )
        await ctx.emit_status(state, "received")
    return state
