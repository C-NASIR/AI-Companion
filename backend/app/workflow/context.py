"""Shared helpers for workflow activities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Mapping, Sequence

from ..events import EventBus, new_event
from ..mcp.schema import ToolDescriptor
from ..retrieval import RetrievalStore
from ..state import RunPhase, RunState
from ..state_store import StateStore


class ActivityContext:
    """Lightweight helper for workflow activities."""

    def __init__(
        self,
        bus: EventBus,
        state_store: StateStore,
        retrieval_store: RetrievalStore,
        allowed_tools_provider: Callable[[RunState], Sequence[ToolDescriptor]]
        | None = None,
    ):
        self.bus = bus
        self.state_store = state_store
        self.retrieval_store = retrieval_store
        self._allowed_tools_provider = allowed_tools_provider

    async def emit_event(self, state: RunState, event_type: str, data: Mapping[str, object]) -> None:
        """Publish an event with run metadata."""
        await self.bus.publish(new_event(event_type, state.run_id, data))

    async def emit_status(self, state: RunState, value: str) -> None:
        await self.emit_event(state, "status.changed", {"value": value})

    async def emit_decision(
        self, state: RunState, name: str, value: str, notes: str | None = None
    ) -> None:
        payload: dict[str, object] = {"name": name, "value": value}
        if notes:
            payload["notes"] = notes
        await self.emit_event(state, "decision.made", payload)

    async def emit_output(self, state: RunState, text: str) -> None:
        await self.emit_event(state, "output.chunk", {"text": text})

    async def emit_error(self, state: RunState, node_name: str, message: str) -> None:
        await self.emit_event(state, "error.raised", {"node": node_name, "message": message})

    def save_state(self, state: RunState) -> None:
        """Persist the latest run snapshot."""
        self.state_store.save(state)

    def allowed_tools(self, state: RunState) -> list[ToolDescriptor]:
        """Return allowed tools for the provided state."""
        if not self._allowed_tools_provider:
            return []
        return list(self._allowed_tools_provider(state))

    @asynccontextmanager
    async def step_scope(self, state: RunState, name: str, phase: RunPhase):
        """Emit lifecycle events mirroring the previous node_scope."""
        state.transition_phase(phase)
        await self.emit_event(state, "node.started", {"name": name})
        try:
            yield
        finally:
            self.save_state(state)
            await self.emit_event(state, "node.completed", {"name": name})
