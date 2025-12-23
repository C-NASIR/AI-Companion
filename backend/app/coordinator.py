"""Run coordinator that advances the intelligence graph via events."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Mapping

from .events import Event, EventBus, new_event
from .intelligence import GRAPH, NODE_MAP, NodeContext
from .state import RunPhase, RunState
from .state_store import StateStore

logger = logging.getLogger(__name__)

NODE_SEQUENCE = [spec.name for spec in GRAPH]
NEXT_NODE: dict[str, str | None] = {
    current: NODE_SEQUENCE[idx + 1] if idx + 1 < len(NODE_SEQUENCE) else None
    for idx, current in enumerate(NODE_SEQUENCE)
}


class RunCoordinator:
    """Coordinates node execution driven by the event log."""

    def __init__(self, bus: EventBus, state_store: StateStore):
        self.bus = bus
        self.state_store = state_store
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start_run(self, state: RunState) -> None:
        """Persist initial state, emit run.started, and schedule coordination loop."""
        run_id = state.run_id
        if run_id in self._tasks:
            logger.warning("run already active", extra={"run_id": run_id})
            return

        self.state_store.save(state)
        queue: asyncio.Queue[Event] = asyncio.Queue()

        async def _subscriber(event: Event) -> None:
            await queue.put(event)

        unsubscribe = self.bus.subscribe(run_id, _subscriber)
        ctx = NodeContext(self.bus, self.state_store)
        task = asyncio.create_task(
            self._run_loop(state, queue, unsubscribe, ctx), name=f"run-{run_id}"
        )
        self._tasks[run_id] = task

        await self.bus.publish(
            new_event(
                "run.started",
                run_id,
                {
                    "message": state.message,
                    "context": state.context,
                    "mode": state.mode.value,
                },
            )
        )
        logger.info("run scheduled", extra={"run_id": run_id})

    async def _run_loop(
        self,
        state: RunState,
        queue: asyncio.Queue[Event],
        unsubscribe: Callable[[], None],
        ctx: NodeContext,
    ) -> None:
        run_id = state.run_id
        try:
            while True:
                event = await queue.get()
                if event.type in {"run.completed", "run.failed"}:
                    logger.info(
                        "run finished via event type=%s", event.type, extra={"run_id": run_id}
                    )
                    break
                if event.type in {"tool.completed", "tool.failed"}:
                    await self._handle_tool_event(state, ctx, event)
                next_node = self._next_node_for_event(state, event)
                if not next_node:
                    continue
                spec = NODE_MAP.get(next_node)
                if not spec:
                    logger.warning(
                        "unknown node referenced=%s", next_node, extra={"run_id": run_id}
                    )
                    continue
                try:
                    await spec.func(state, ctx)
                except Exception:
                    logger.exception(
                        "node %s failed", next_node, extra={"run_id": run_id}
                    )
                    await self.bus.publish(
                        new_event(
                            "error.raised",
                            run_id,
                            {"node": next_node, "message": "internal error"},
                        )
                    )
                    await self.bus.publish(
                        new_event(
                            "run.failed",
                            run_id,
                            {"final_text": state.output_text, "reason": "internal error"},
                        )
                    )
                    break
        finally:
            unsubscribe()
            self._tasks.pop(run_id, None)
            logger.info("run coordinator loop ended", extra={"run_id": run_id})

    async def _handle_tool_event(self, state: RunState, ctx: NodeContext, event: Event) -> None:
        run_id = state.run_id
        tool_name = event.data.get("tool_name")
        if not isinstance(tool_name, str):
            tool_name = "unknown"
        duration_ms = int(event.data.get("duration_ms") or 0)
        if event.type == "tool.completed":
            output = event.data.get("output")
            if not isinstance(output, Mapping):
                output = {}
            state.record_tool_result(
                name=tool_name,
                status="completed",
                payload=output,
                duration_ms=duration_ms,
            )
            notes = f"{tool_name} completed"
            state.record_decision("tool_result", "completed", notes=notes)
            await ctx.emit_decision(state, "tool_result", "completed", notes)
            logger.info(
                "tool completed recorded tool=%s duration_ms=%s",
                tool_name,
                duration_ms,
                extra={"run_id": run_id},
            )
        else:
            error = event.data.get("error")
            if not isinstance(error, Mapping):
                error = {"error": "unknown"}
            state.record_tool_result(
                name=tool_name,
                status="failed",
                payload=error,
                duration_ms=duration_ms,
            )
            error_reason = error.get("error")
            reason_str = (
                error_reason if isinstance(error_reason, str) else "tool_failed"
            )
            state.record_decision("tool_result", "failed", notes=reason_str)
            await ctx.emit_decision(state, "tool_result", "failed", reason_str)
            state.set_verification(passed=False, reason="tool_failed")
            logger.warning(
                "tool failed tool=%s reason=%s",
                tool_name,
                reason_str,
                extra={"run_id": run_id},
            )
        state.transition_phase(RunPhase.RESPOND)
        ctx.save_state(state)

    @staticmethod
    def _next_node_for_event(state: RunState, event: Event) -> str | None:
        if event.type == "run.started":
            return NODE_SEQUENCE[0]
        if event.type == "tool.completed":
            return "verify"
        if event.type == "tool.failed":
            return "finalize"
        if event.type == "node.completed":
            if state.phase == RunPhase.WAITING_FOR_TOOL:
                return None
            completed_name = event.data.get("name")
            if isinstance(completed_name, str):
                return NEXT_NODE.get(completed_name)
        return None
