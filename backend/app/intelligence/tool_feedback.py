"""Helpers for tool-related response messaging."""

from __future__ import annotations

from ..state import RunState
from .context import NodeContext


def _latest_completed_tool(state: RunState):
    for record in reversed(state.tool_results):
        if record.status == "completed":
            return record
    return None


def _format_tool_result_value(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_tool_summary_text(state: RunState) -> str | None:
    record = _latest_completed_tool(state)
    if not record or not record.output:
        return None
    result_value = record.output.get("result") if isinstance(record.output, dict) else None
    if isinstance(result_value, (int, float)):
        return f"The result is {_format_tool_result_value(result_value)}."
    return f"{record.name.capitalize()} executed successfully."


def build_tool_failure_text(state: RunState) -> str | None:
    if not state.tool_results:
        return None
    record = state.tool_results[-1]
    if record.status != "failed" or not record.error:
        return None
    reason = record.error.get("error") if isinstance(record.error, dict) else None
    reason_text = f": {reason}" if isinstance(reason, str) and reason else ""
    return f"{record.name.capitalize()} failed{reason_text}."


async def append_tool_summary_if_needed(state: RunState, ctx: NodeContext) -> None:
    if state.last_tool_status != "completed":
        return
    if state.output_text.strip():
        return
    summary = build_tool_summary_text(state)
    if not summary:
        return
    state.append_output(summary)
    await ctx.emit_output(state, summary)
