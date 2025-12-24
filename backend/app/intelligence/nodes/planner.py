"""Planning node logic for the intelligence graph."""

from __future__ import annotations

from ...events import tool_discovered_event, tool_requested_event
from ...schemas import ChatMode
from ...state import PlanType, RunPhase, RunState
from ..context import NodeContext
from ..intents import match_tool_intent
from ..utils import log_run


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
            state.record_tool_request(
                name=descriptor.name,
                arguments=arguments,
                source=descriptor.source,
                permission_scope=descriptor.permission_scope,
            )
            state.transition_phase(RunPhase.WAITING_FOR_TOOL)
            await ctx.emit_status(state, "thinking")
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
            return state
    return state
