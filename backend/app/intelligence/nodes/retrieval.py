"""Retrieval node logic for the intelligence graph."""

from __future__ import annotations

from ...events import retrieval_completed_event, retrieval_started_event
from ...state import RunPhase, RunState
from ..context import NodeContext
from ..utils import log_run

RETRIEVAL_TOP_K = 3


def _build_retrieval_query(state: RunState) -> str:
    message = state.message.strip()
    if state.context:
        context = state.context.strip()
        return f"{message}\n\nContext:\n{context}"
    return message


async def retrieve_node(state: RunState, ctx: NodeContext) -> RunState:
    """Fetch supporting evidence."""
    async with ctx.node_scope(state, "retrieve", RunPhase.RETRIEVE):
        query = _build_retrieval_query(state)
        await ctx.bus.publish(retrieval_started_event(state.run_id, query))
        log_run(
            state.run_id,
            "retrieval querying top_k=%s query_length=%s",
            RETRIEVAL_TOP_K,
            len(query),
        )
        try:
            chunks = ctx.retrieval_store.query(query, top_k=RETRIEVAL_TOP_K)
        except Exception as exc:  # pragma: no cover - defensive guard
            message = f"retrieval_failed: {exc}"
            await ctx.emit_error(state, "retrieve", message)
            log_run(state.run_id, "retrieval error=%s", exc)
            raise
        state.set_retrieved_chunks(chunks)
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        await ctx.bus.publish(retrieval_completed_event(state.run_id, chunk_ids))
        decision_value = str(len(chunk_ids))
        notes = f"{decision_value} chunk(s) retrieved"
        state.record_decision("retrieval_chunks", decision_value, notes=notes)
        await ctx.emit_decision(state, "retrieval_chunks", decision_value, notes)
    return state
