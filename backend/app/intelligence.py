"""Event-driven intelligence graph nodes for Session 3."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from .events import EventBus, new_event
from .model import stream_chat
from .schemas import ChatMode
from .state import PlanType, RunPhase, RunState
from .state_store import StateStore

logger = logging.getLogger(__name__)

NodeFunc = Callable[[RunState, "NodeContext"], Awaitable[RunState]]


@dataclass(frozen=True)
class NodeSpec:
    """Definition of a control graph node."""

    name: str
    phase: RunPhase
    func: NodeFunc


class NodeContext:
    """Shared helpers for node logic."""

    def __init__(self, bus: EventBus, state_store: StateStore):
        self.bus = bus
        self.state_store = state_store

    async def emit(self, state: RunState, event_type: str, data: Mapping[str, object]) -> None:
        """Publish an event through the bus."""
        await self.bus.publish(new_event(event_type, state.run_id, data))

    async def emit_status(self, state: RunState, value: str) -> None:
        await self.emit(state, "status.changed", {"value": value})

    async def emit_decision(
        self, state: RunState, name: str, value: str, notes: str | None = None
    ) -> None:
        payload: dict[str, object] = {"name": name, "value": value}
        if notes:
            payload["notes"] = notes
        await self.emit(state, "decision.made", payload)

    async def emit_output(self, state: RunState, text: str) -> None:
        await self.emit(state, "output.chunk", {"text": text})

    async def emit_error(self, state: RunState, node_name: str, message: str) -> None:
        await self.emit(
            state,
            "error.raised",
            {"node": node_name, "message": message},
        )

    def save_state(self, state: RunState) -> None:
        """Persist the latest snapshot."""
        self.state_store.save(state)

    @asynccontextmanager
    async def node_scope(self, state: RunState, name: str, phase: RunPhase):
        """Emit lifecycle events and persist state after execution."""
        state.transition_phase(phase)
        await self.emit(state, "node.started", {"name": name})
        try:
            yield
        finally:
            self.save_state(state)
            await self.emit(state, "node.completed", {"name": name})


def _log(run_id: str, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})


async def receive_node(state: RunState, ctx: NodeContext) -> RunState:
    """Capture the request intent."""
    async with ctx.node_scope(state, "receive", RunPhase.RECEIVE):
        _log(
            state.run_id,
            "node receive message_length=%s context_length=%s mode=%s",
            len(state.message),
            len(state.context or ""),
            state.mode.value,
        )
        await ctx.emit_status(state, "received")
    return state


def _choose_plan(state: RunState) -> tuple[PlanType, str]:
    """Simple heuristic-based planner."""
    message = state.message.strip()
    if not message:
        return (PlanType.CANNOT_ANSWER, "empty message")
    if len(message) < 6:
        return (PlanType.NEEDS_CLARIFICATION, "very short message")
    if state.mode == ChatMode.RESEARCH and not state.context:
        return (PlanType.NEEDS_CLARIFICATION, "research mode without context")
    lowered = message.lower()
    if any(keyword in lowered for keyword in ("illegal", "forbidden", "unsafe")):
        return (PlanType.CANNOT_ANSWER, "potentially unsafe request")
    if message.endswith("?"):
        return (PlanType.DIRECT_ANSWER, "question detected")
    return (PlanType.DIRECT_ANSWER, "default direct answer path")


async def plan_node(state: RunState, ctx: NodeContext) -> RunState:
    """Decide which strategy to use."""
    async with ctx.node_scope(state, "plan", RunPhase.PLAN):
        await ctx.emit_status(state, "thinking")
        plan_type, reason = _choose_plan(state)
        state.set_plan_type(plan_type)
        state.record_decision("plan_type", plan_type.value, notes=reason)
        await ctx.emit_decision(state, "plan_type", plan_type.value, reason)
        _log(state.run_id, "plan decided plan=%s reason=%s", plan_type.value, reason)
    return state


async def _stream_direct_answer(state: RunState, ctx: NodeContext) -> None:
    first_chunk = True
    async for chunk in stream_chat(
        state.message, state.context, state.mode, state.run_id
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
        _log(state.run_id, "respond strategy=%s", plan.value)
        if plan == PlanType.DIRECT_ANSWER:
            await _stream_direct_answer(state, ctx)
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


def _evaluate_verification(state: RunState) -> tuple[bool, str | None]:
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
        passed, reason = _evaluate_verification(state)
        state.set_verification(passed=passed, reason=reason)
        decision_value = "pass" if passed else "fail"
        state.record_decision("verification", decision_value, notes=reason)
        await ctx.emit_decision(state, "verification", decision_value, reason)
        _log(state.run_id, "verification result=%s", decision_value)
    return state


async def finalize_node(state: RunState, ctx: NodeContext) -> RunState:
    """Emit outcome and completion events."""
    async with ctx.node_scope(state, "finalize", RunPhase.FINALIZE):
        passed = bool(state.verification_passed)
        outcome = "success" if passed else "failed"
        reason = state.verification_reason if not passed else None
        state.set_outcome(outcome, reason)
        state.record_decision("outcome", outcome, notes=reason)
        await ctx.emit_decision(state, "outcome", outcome, reason)
        payload: dict[str, object] = {"final_text": state.output_text}
        if reason:
            payload["reason"] = reason
        event_type = "run.completed" if passed else "run.failed"
        await ctx.emit(state, event_type, payload)
        await ctx.emit_status(state, "complete")
        _log(state.run_id, "finalize outcome=%s", outcome)
    return state


GRAPH: list[NodeSpec] = [
    NodeSpec("receive", RunPhase.RECEIVE, receive_node),
    NodeSpec("plan", RunPhase.PLAN, plan_node),
    NodeSpec("respond", RunPhase.RESPOND, respond_node),
    NodeSpec("verify", RunPhase.VERIFY, verify_node),
    NodeSpec("finalize", RunPhase.FINALIZE, finalize_node),
]

NODE_SEQUENCE = [spec.name for spec in GRAPH]
NODE_MAP = {spec.name: spec for spec in GRAPH}
NEXT_NODE: dict[str, str] = {
    current: NODE_SEQUENCE[idx + 1]
    for idx, current in enumerate(NODE_SEQUENCE[:-1])
}
