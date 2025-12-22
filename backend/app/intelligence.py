"""Intelligence layer control graph for Session 2."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .model import stream_chat
from .schemas import ChatMode
from .state import PlanType, RunPhase, RunState

logger = logging.getLogger(__name__)

EmitFunc = Callable[[str, dict[str, Any]], Awaitable[None]]
NodeFunc = Callable[[RunState, EmitFunc], Awaitable[RunState]]

GraphNode = tuple[str, RunPhase, NodeFunc]

NODE_STEP_LABELS: dict[str, str] = {
    "receive": "Receive",
    "plan": "Plan",
    "respond": "Respond",
    "verify": "Verify",
    "finalize": "Finalize",
}


def _log(run_id: str, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})


async def _emit_step_state(
    emit: EmitFunc, node_name: str, state: str
) -> None:
    label = NODE_STEP_LABELS.get(node_name)
    if not label:
        return
    await emit("step", {"label": label, "state": state})


async def _emit_node_state(
    emit: EmitFunc, node_name: str, state: str
) -> None:
    await emit("node", {"name": node_name, "state": state})


async def receive_node(state: RunState, emit: EmitFunc) -> RunState:
    """Capture the intent and acknowledge receipt."""
    _log(
        state.run_id,
        "node receive message_length=%s context_length=%s mode=%s",
        len(state.message),
        len(state.context or ""),
        state.mode.value,
    )
    await emit("status", {"value": "received"})
    return state


def _choose_plan(state: RunState) -> tuple[PlanType, str]:
    """Lightweight heuristic to select a plan."""
    message = state.message.strip()
    if not message:
        return (PlanType.CANNOT_ANSWER, "empty message")
    if state.mode == ChatMode.RESEARCH and not state.context:
        return (PlanType.NEEDS_CLARIFICATION, "research without context")
    if "clarify" in message.lower():
        return (PlanType.NEEDS_CLARIFICATION, "message requests clarification")
    if any(term in message.lower() for term in ("cannot", "impossible")):
        return (PlanType.CANNOT_ANSWER, "message indicates impossibility")
    return (PlanType.DIRECT_ANSWER, "default direct answer path")


async def plan_node(state: RunState, emit: EmitFunc) -> RunState:
    """Decide which response strategy should be used."""
    await emit("status", {"value": "thinking"})
    plan_type, reason = _choose_plan(state)
    state.set_plan_type(plan_type)
    state.record_decision("plan_type", plan_type.value, notes=reason)
    await emit(
        "decision",
        {"name": "plan_type", "value": plan_type.value, "notes": reason},
    )
    _log(
        state.run_id,
        "plan decided=%s reason=%s",
        plan_type.value,
        reason,
    )
    return state


async def _stream_direct_answer(
    state: RunState, emit: EmitFunc
) -> None:
    first_chunk = True
    async for chunk in stream_chat(
        state.message, state.context, state.mode, state.run_id
    ):
        if first_chunk:
            await emit("status", {"value": "responding"})
            first_chunk = False
        state.append_output(chunk)
        await emit("output", {"text": chunk})


async def _stream_synthetic_response(
    state: RunState, emit: EmitFunc, template: str
) -> None:
    await emit("status", {"value": "responding"})
    snippets = [
        template.format(
            mode=state.mode.value,
            snippet=state.message.strip()[:60] or "…",
            run_id=state.run_id,
        )
    ]
    for text in snippets:
        for chunk in _chunk_text(text, 32):
            state.append_output(chunk)
            await emit("output", {"text": chunk})
            await asyncio.sleep(0.1)


def _chunk_text(text: str, size: int) -> list[str]:
    """Split text into small chunks to simulate streaming."""
    return [text[i : i + size] for i in range(0, len(text), size)]


async def respond_node(state: RunState, emit: EmitFunc) -> RunState:
    """Produce output according to the chosen strategy."""
    plan = state.plan_type or PlanType.DIRECT_ANSWER
    _log(state.run_id, "respond plan=%s", plan.value)
    if plan == PlanType.DIRECT_ANSWER:
        await _stream_direct_answer(state, emit)
    elif plan == PlanType.NEEDS_CLARIFICATION:
        template = (
            "I am in {mode} mode and need more details about: {snippet}. "
            "Please clarify so I can proceed (run {run_id})."
        )
        await _stream_synthetic_response(state, emit, template)
    else:
        template = (
            "Given the current information ({snippet}) and {mode} mode, "
            "I cannot provide a reliable answer (trace {run_id})."
        )
        await _stream_synthetic_response(state, emit, template)
    return state


async def verify_node(state: RunState, emit: EmitFunc) -> RunState:
    """Perform a simple verification pass."""
    passed = bool(state.output_text.strip())
    reason = None if passed else "response text is empty"
    state.set_verification(passed=passed, reason=reason)
    decision_value = "pass" if passed else "fail"
    state.record_decision("verification", decision_value, notes=reason)
    await emit(
        "decision",
        {
            "name": "verification",
            "value": decision_value,
            **({"notes": reason} if reason else {}),
        },
    )
    _log(state.run_id, "verification %s", decision_value)
    return state


async def finalize_node(state: RunState, emit: EmitFunc) -> RunState:
    """Emit completion events and final outcome."""
    passed = bool(state.verification_passed)
    outcome = "success" if passed else "failed"
    notes = state.verification_reason
    await emit(
        "decision",
        {
            "name": "outcome",
            "value": outcome,
            **({"notes": notes} if notes else {}),
        },
    )
    await emit("status", {"value": "complete"})
    done_payload: dict[str, Any] = {
        "final_text": state.output_text,
        "outcome": outcome,
    }
    if notes:
        done_payload["reason"] = notes
    await emit("done", done_payload)
    _log(state.run_id, "finalize outcome=%s", outcome)
    return state


GRAPH: list[GraphNode] = [
    ("receive", RunPhase.RECEIVE, receive_node),
    ("plan", RunPhase.PLAN, plan_node),
    ("respond", RunPhase.RESPOND, respond_node),
    ("verify", RunPhase.VERIFY, verify_node),
    ("finalize", RunPhase.FINALIZE, finalize_node),
]


async def run_graph(state: RunState, emit: EmitFunc) -> RunState:
    """Execute the fixed control graph."""
    for node_name, phase, func in GRAPH:
        state.transition_phase(phase)
        await _emit_node_state(emit, node_name, "started")
        await _emit_step_state(emit, node_name, "started")
        try:
            state = await func(state, emit)
        except Exception:
            logger.exception("node %s failed", node_name, extra={"run_id": state.run_id})
            raise
        await _emit_node_state(emit, node_name, "completed")
        await _emit_step_state(emit, node_name, "completed")
    return state
