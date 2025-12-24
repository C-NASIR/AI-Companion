"""Workflow activity implementations derived from Session 3 nodes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..events import (
    retrieval_completed_event,
    retrieval_started_event,
    tool_discovered_event,
    tool_requested_event,
)
from ..model import stream_chat
from ..schemas import ChatMode
from ..state import PlanType, RunPhase, RunState
from ..intelligence.intents import match_tool_intent
from ..intelligence.tool_feedback import (
    build_tool_failure_text,
    build_tool_summary_text,
)
from ..intelligence.utils import log_run
from .context import ActivityContext
from .engine import ExternalEventRequired, HumanApprovalRequired
from .models import ActivityFunc, WorkflowState, WorkflowStatus


def _chunk_text(text: str, size: int = 64) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def _gather_response_text(
    state: RunState,
    retrieved_chunks: Sequence[Mapping[str, Any]],
) -> str:
    chunks: list[str] = []
    async for chunk in stream_chat(
        state.message,
        state.context,
        state.mode,
        state.run_id,
        retrieved_chunks,
    ):
        chunks.append(chunk)
    return "".join(chunks)


def create_receive_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "receive", RunPhase.RECEIVE):
            log_run(
                state.run_id,
                "node receive message_length=%s context_length=%s mode=%s",
                len(state.message),
                len(state.context or ""),
                state.mode.value,
            )
            await ctx.emit_status(state, "received")
        return state, workflow_state

    return _activity


def create_plan_activity(ctx: ActivityContext) -> ActivityFunc:
    from ..intelligence.nodes.planner import _choose_plan  # reuse planner heuristics

    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "plan", RunPhase.PLAN):
            await ctx.emit_status(state, "thinking")
            plan_type, reason = _choose_plan(state)
            state.set_plan_type(plan_type)
            state.record_decision("plan_type", plan_type.value, notes=reason)
            await ctx.emit_decision(state, "plan_type", plan_type.value, reason)
            log_run(state.run_id, "plan decided plan=%s reason=%s", plan_type.value, reason)

            allowed_tools = ctx.allowed_tools(state)
            state.set_available_tools(allowed_tools)
            tool_names = [descriptor.name for descriptor in allowed_tools]
            available_value = ", ".join(tool_names) if tool_names else "none"
            notes = f"{len(tool_names)} tool(s) available"
            state.record_decision("available_tools", available_value, notes=notes)
            await ctx.emit_decision(state, "available_tools", available_value, notes)
            for descriptor in allowed_tools:
                await ctx.bus.publish(
                    tool_discovered_event(
                        state.run_id,
                        tool_name=descriptor.name,
                        source=descriptor.source,
                        permission_scope=descriptor.permission_scope,
                    )
                )

            tool_selection = None
            if plan_type == PlanType.DIRECT_ANSWER:
                tool_selection = match_tool_intent(state.message, allowed_tools)
            selected_name = tool_selection[0].name if tool_selection else "none"
            selection_notes = (
                f"{selected_name} selected" if tool_selection else "no matching tool"
            )
            state.record_decision("tool_selected", selected_name, notes=selection_notes)
            await ctx.emit_decision(state, "tool_selected", selected_name, selection_notes)

            if tool_selection:
                descriptor, arguments = tool_selection
                already_requested = (
                    state.requested_tool == descriptor.name
                    and state.last_tool_status == "requested"
                )
                if not already_requested:
                    state.record_tool_request(
                        name=descriptor.name,
                        arguments=arguments,
                        source=descriptor.source,
                        permission_scope=descriptor.permission_scope,
                    )
                    await ctx.bus.publish(
                        tool_requested_event(
                            state.run_id,
                            tool_name=descriptor.name,
                            arguments=arguments,
                            source=descriptor.source,
                            permission_scope=descriptor.permission_scope,
                        )
                    )
                    log_run(
                        state.run_id,
                        "requested tool name=%s args=%s",
                        descriptor.name,
                        arguments,
                    )
                    await ctx.emit_status(state, "waiting_for_tool")
            return state, workflow_state

    return _activity


def create_retrieve_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "retrieve", RunPhase.RETRIEVE):
            if state.last_tool_status == "requested":
                raise ExternalEventRequired(
                    ("tool.completed", "tool.failed", "tool.denied"),
                    reason="waiting_for_tool",
                )
            query = state.message.strip()
            if state.context:
                query = f"{query}\n\nContext:\n{state.context.strip()}"
            await ctx.bus.publish(retrieval_started_event(state.run_id, query))
            log_run(
                state.run_id,
                "retrieval querying top_k=%s query_length=%s",
                3,
                len(query),
            )
            try:
                chunks = ctx.retrieval_store.query(query, top_k=3)
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
        return state, workflow_state

    return _activity


def create_respond_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "respond", RunPhase.RESPOND):
            if state.last_tool_status == "requested":
                raise ExternalEventRequired(
                    ("tool.completed", "tool.failed", "tool.denied"),
                    reason="waiting_for_tool",
                )
            plan = state.plan_type or PlanType.DIRECT_ANSWER
            log_run(state.run_id, "respond strategy=%s", plan.value)
            strategy = "model_stream"
            notes: str | None = None

            if state.last_tool_status == "completed" and not state.output_text.strip():
                summary = build_tool_summary_text(state)
                if summary:
                    state.append_output(summary)
                    for chunk in _chunk_text(summary):
                        await ctx.emit_output(state, chunk)
                strategy = "tool_summary"
                notes = state.requested_tool or "tool_result"
            elif plan == PlanType.DIRECT_ANSWER:
                response_text = await _gather_response_text(state, state.retrieved_chunks)
                if response_text:
                    await ctx.emit_status(state, "responding")
                    state.append_output(response_text)
                    for chunk in _chunk_text(response_text):
                        await ctx.emit_output(state, chunk)
            elif plan == PlanType.NEEDS_CLARIFICATION:
                strategy = "clarify_static"
                notes = "requesting additional details"
                template = (
                    "Mode {mode}: I need more details about \"{snippet}\" to continue. "
                    "Please clarify so run {run_id} can proceed."
                )
                snippet = state.message.strip()[:80] or "..."
                full = template.format(
                    mode=state.mode.value,
                    snippet=snippet,
                    run_id=state.run_id,
                )
                await ctx.emit_status(state, "responding")
                state.append_output(full)
                for chunk in _chunk_text(full):
                    await ctx.emit_output(state, chunk)
            else:
                strategy = "refuse_static"
                notes = "insufficient or unsafe request"
                template = (
                    "Mode {mode}: I cannot produce a reliable response for \"{snippet}\". "
                    "Run {run_id} must stop here."
                )
                snippet = state.message.strip()[:80] or "..."
                full = template.format(
                    mode=state.mode.value,
                    snippet=snippet,
                    run_id=state.run_id,
                )
                await ctx.emit_status(state, "responding")
                state.append_output(full)
                for chunk in _chunk_text(full):
                    await ctx.emit_output(state, chunk)

            state.record_decision("response_strategy", strategy, notes=notes)
            await ctx.emit_decision(state, "response_strategy", strategy, notes)
        return state, workflow_state

    return _activity


def _evaluate_grounding_requirements(state: RunState) -> tuple[bool, str | None]:
    import re

    pattern = re.compile(r"\[([\w\-\.:]+)\]")
    text = state.output_text
    if not text:
        return True, None
    citations = pattern.findall(text)
    if not state.retrieved_chunks:
        return True, None
    if not citations:
        return False, "missing_citations"
    valid_ids = {chunk.chunk_id for chunk in state.retrieved_chunks}
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


def create_verify_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "verify", RunPhase.VERIFY):
            if state.last_tool_status == "completed" and not state.output_text.strip():
                summary = build_tool_summary_text(state)
                if summary:
                    state.append_output(summary)
                    await ctx.emit_output(state, summary)

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
        return state, workflow_state

    return _activity


def _approval_required(state: RunState) -> bool:
    if state.verification_passed:
        return False
    if state.mode == ChatMode.RESEARCH:
        return True
    if state.plan_type == PlanType.DIRECT_ANSWER and not state.verification_passed:
        return True
    return False


def create_maybe_approve_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "maybe_approve", RunPhase.APPROVAL):
            if not _approval_required(state):
                state.record_decision("human_approval", "skipped", notes="not_required")
                await ctx.emit_decision(state, "human_approval", "skipped", "not_required")
                return state, workflow_state
            if workflow_state.human_decision:
                decision = workflow_state.human_decision
                notes = "approval_recorded"
                if decision == "approved":
                    state.set_verification(passed=True, reason="human_override")
                state.record_decision("human_approval", decision, notes=notes)
                await ctx.emit_decision(state, "human_approval", decision, notes)
                return state, workflow_state
            raise HumanApprovalRequired("verification_failed")

    return _activity


def create_finalize_activity(ctx: ActivityContext) -> ActivityFunc:
    async def _activity(state: RunState, workflow_state: WorkflowState):
        async with ctx.step_scope(state, "finalize", RunPhase.FINALIZE):
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
            await ctx.emit_event(state, event_type, payload)
            await ctx.emit_status(state, "complete")
            log_run(state.run_id, "finalize outcome=%s", outcome)
            workflow_state.status = (
                WorkflowStatus.COMPLETED if passed else WorkflowStatus.FAILED
            )
        return state, workflow_state

    return _activity


def build_activity_map(ctx: ActivityContext) -> dict[str, ActivityFunc]:
    """Helper to register all workflow steps at once."""
    return {
        "receive": create_receive_activity(ctx),
        "plan": create_plan_activity(ctx),
        "retrieve": create_retrieve_activity(ctx),
        "respond": create_respond_activity(ctx),
        "verify": create_verify_activity(ctx),
        "maybe_approve": create_maybe_approve_activity(ctx),
        "finalize": create_finalize_activity(ctx),
    }
