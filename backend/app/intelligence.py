"""Event-driven intelligence graph nodes for Session 3."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Sequence

from .events import (
    EventBus,
    new_event,
    retrieval_completed_event,
    retrieval_started_event,
    tool_discovered_event,
    tool_requested_event,
)
from .mcp.schema import ToolDescriptor
from .model import stream_chat
from .retrieval import RetrievalStore
from .schemas import ChatMode
from .state import PlanType, RunPhase, RunState
from .state_store import StateStore

logger = logging.getLogger(__name__)

NodeFunc = Callable[[RunState, "NodeContext"], Awaitable[RunState]]
RETRIEVAL_TOP_K = 3


@dataclass(frozen=True)
class NodeSpec:
    """Definition of a control graph node."""

    name: str
    phase: RunPhase
    func: NodeFunc


class NodeContext:
    """Shared helpers for node logic."""

    def __init__(
        self,
        bus: EventBus,
        state_store: StateStore,
        retrieval_store: RetrievalStore,
        allowed_tools_provider: Callable[[RunState], Sequence[ToolDescriptor]] | None = None,
    ):
        self.bus = bus
        self.state_store = state_store
        self.retrieval_store = retrieval_store
        self._allowed_tools_provider = allowed_tools_provider

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

    def allowed_tools(self, state: RunState) -> list[ToolDescriptor]:
        """Return the list of allowed tools for this run."""
        if not self._allowed_tools_provider:
            return []
        return list(self._allowed_tools_provider(state))

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
            tool_selection = _match_tool_intent(state.message, allowed_tools)
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
            _log(
                state.run_id,
                "requested tool name=%s args=%s",
                descriptor.name,
                arguments,
            )
            return state
    return state


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
        _log(
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
            _log(state.run_id, "retrieval error=%s", exc)
            raise
        state.set_retrieved_chunks(chunks)
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        await ctx.bus.publish(retrieval_completed_event(state.run_id, chunk_ids))
        decision_value = str(len(chunk_ids))
        notes = f"{decision_value} chunk(s) retrieved"
        state.record_decision("retrieval_chunks", decision_value, notes=notes)
        await ctx.emit_decision(state, "retrieval_chunks", decision_value, notes)
    return state


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


_SYMBOL_EXPR = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)")
_KEYWORD_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"\badd\s+(-?\d+(?:\.\d+)?)\s+(?:and|to)\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "add",
        "normal",
    ),
    (
        re.compile(
            r"\bsubtract\s+(-?\d+(?:\.\d+)?)\s+from\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "subtract",
        "reverse",
    ),
    (
        re.compile(
            r"\b(?:multiply|times)\s+(-?\d+(?:\.\d+)?)\s+(?:and|by)\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "multiply",
        "normal",
    ),
    (
        re.compile(
            r"\bdivide\s+(-?\d+(?:\.\d+)?)\s+by\s+(-?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "divide",
        "normal",
    ),
]


def _parse_number(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _match_symbol_expression(message: str) -> dict[str, float] | None:
    match = _SYMBOL_EXPR.search(message)
    if not match:
        return None
    a = _parse_number(match.group(1))
    b = _parse_number(match.group(3))
    op = match.group(2)
    if a is None or b is None:
        return None
    mapping = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
    operation = mapping.get(op)
    if not operation:
        return None
    return {"operation": operation, "a": a, "b": b}


def _match_keyword_expression(message: str) -> dict[str, float] | None:
    for pattern, operation, order in _KEYWORD_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        first = match.group(1)
        second = match.group(2)
        a = _parse_number(first)
        b = _parse_number(second)
        if a is None or b is None:
            continue
        if order == "reverse":
            a, b = b, a
        return {"operation": operation, "a": a, "b": b}
    return None


def _detect_calculator_request(message: str) -> dict[str, float] | None:
    symbol_match = _match_symbol_expression(message)
    if symbol_match:
        return symbol_match
    return _match_keyword_expression(message)


_REPO_KEYWORD_PATTERN = re.compile(
    r"(?:repo|repository)\s+(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_REPO_URL_PATTERN = re.compile(
    r"github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_REPO_LOOSE_PATTERN = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")
_PATH_HINT_PATTERN = re.compile(
    r"(?:path|directory|folder)\s+(?P<path>[A-Za-z0-9_.\-/]+)",
    re.IGNORECASE,
)
_FILE_HINT_PATTERN = re.compile(
    r"file\s+(?:at\s+|from\s+)?(?P<path>[A-Za-z0-9_.\-/]+)",
    re.IGNORECASE,
)


def _extract_repo_identifier(message: str) -> str | None:
    url_match = _REPO_URL_PATTERN.search(message)
    if url_match:
        return url_match.group("repo")
    keyword_match = _REPO_KEYWORD_PATTERN.search(message)
    if keyword_match:
        return keyword_match.group("repo")
    lowered = message.lower()
    if "repo" in lowered or "repository" in lowered or "github" in lowered:
        loose_match = _REPO_LOOSE_PATTERN.search(message)
        if loose_match:
            return loose_match.group(1)
    return None


def _extract_path_hint(message: str) -> str | None:
    match = _PATH_HINT_PATTERN.search(message)
    if match:
        return match.group("path").strip().strip("\"'")
    return None


def _extract_file_path(message: str) -> str | None:
    match = _FILE_HINT_PATTERN.search(message)
    if not match:
        return None
    return match.group("path").strip().strip("\"'")


def _detect_github_list_files(message: str) -> dict[str, str] | None:
    lowered = message.lower()
    if not any(keyword in lowered for keyword in ("list", "show", "what are")):
        return None
    if not any(keyword in lowered for keyword in ("file", "files", "folder", "directory")):
        return None
    repo = _extract_repo_identifier(message)
    if not repo:
        return None
    payload: dict[str, str] = {"repo": repo}
    path = _extract_path_hint(message)
    if path:
        payload["path"] = path
    return payload


def _detect_github_read_file(message: str) -> dict[str, str] | None:
    lowered = message.lower()
    if not any(keyword in lowered for keyword in ("read", "open", "show", "view")):
        return None
    if "file" not in lowered:
        return None
    repo = _extract_repo_identifier(message)
    if not repo:
        return None
    path = _extract_file_path(message) or _extract_path_hint(message)
    if not path:
        return None
    return {"repo": repo, "path": path}


def _match_tool_intent(
    message: str, allowed_tools: Sequence[ToolDescriptor]
) -> tuple[ToolDescriptor, dict[str, object]] | None:
    for descriptor in allowed_tools:
        if descriptor.name == "calculator":
            args = _detect_calculator_request(message)
        elif descriptor.name == "github.list_files":
            args = _detect_github_list_files(message)
        elif descriptor.name == "github.read_file":
            args = _detect_github_read_file(message)
        else:
            args = None
        if args:
            return descriptor, args
    return None


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


def _build_tool_summary_text(state: RunState) -> str | None:
    record = _latest_completed_tool(state)
    if not record or not record.output:
        return None
    result_value = record.output.get("result") if isinstance(record.output, dict) else None
    if isinstance(result_value, (int, float)):
        return f"The result is {_format_tool_result_value(result_value)}."
    return f"{record.name.capitalize()} executed successfully."


def _build_tool_failure_text(state: RunState) -> str | None:
    if not state.tool_results:
        return None
    record = state.tool_results[-1]
    if record.status != "failed" or not record.error:
        return None
    reason = record.error.get("error") if isinstance(record.error, dict) else None
    reason_text = f": {reason}" if isinstance(reason, str) and reason else ""
    return f"{record.name.capitalize()} failed{reason_text}."


async def _append_tool_summary_if_needed(state: RunState, ctx: NodeContext) -> None:
    if state.last_tool_status != "completed":
        return
    if state.output_text.strip():
        return
    summary = _build_tool_summary_text(state)
    if not summary:
        return
    state.append_output(summary)
    await ctx.emit_output(state, summary)


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


async def verify_node(state: RunState, ctx: NodeContext) -> RunState:
    """Perform lightweight verification."""
    async with ctx.node_scope(state, "verify", RunPhase.VERIFY):
        await _append_tool_summary_if_needed(state, ctx)
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
        _log(state.run_id, "verification result=%s", decision_value)
    return state


_CITATION_PATTERN = re.compile(r"\[([\w\-\.:]+)\]")


def _extract_cited_chunk_ids(text: str) -> list[str]:
    if not text:
        return []
    return _CITATION_PATTERN.findall(text)


def _evaluate_grounding_requirements(state: RunState) -> tuple[bool, str | None]:
    retrieved = state.retrieved_chunks
    if not retrieved:
        return True, None
    citations = _extract_cited_chunk_ids(state.output_text)
    if not citations:
        return False, "missing_citations"
    valid_ids = {chunk.chunk_id for chunk in retrieved}
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


async def finalize_node(state: RunState, ctx: NodeContext) -> RunState:
    """Emit outcome and completion events."""
    async with ctx.node_scope(state, "finalize", RunPhase.FINALIZE):
        passed = bool(state.verification_passed)
        outcome = "success" if passed else "failed"
        reason = state.verification_reason if not passed else None
        if not passed and state.last_tool_status == "failed":
            failure_text = _build_tool_failure_text(state)
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
        await ctx.emit(state, event_type, payload)
        await ctx.emit_status(state, "complete")
        _log(state.run_id, "finalize outcome=%s", outcome)
    return state


GRAPH: list[NodeSpec] = [
    NodeSpec("receive", RunPhase.RECEIVE, receive_node),
    NodeSpec("plan", RunPhase.PLAN, plan_node),
    NodeSpec("retrieve", RunPhase.RETRIEVE, retrieve_node),
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
