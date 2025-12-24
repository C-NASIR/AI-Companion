"""Tool executor that routes requests through the MCP client."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Mapping

from .events import (
    Event,
    EventBus,
    tool_completed_event,
    tool_denied_event,
    tool_failed_event,
    tool_server_error_event,
)
from .mcp.client import MCPClient
from .mcp.registry import MCPRegistry
from .mcp.server import MCPServerError
from .observability.tracer import Tracer
from .permissions import PermissionGate
from .state_store import StateStore

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes MCP tools in response to tool.requested events."""

    def __init__(
        self,
        bus: EventBus,
        registry: MCPRegistry,
        client: MCPClient,
        permission_gate: PermissionGate,
        state_store: StateStore,
        tracer: Tracer | None = None,
    ):
        self.bus = bus
        self.registry = registry
        self.client = client
        self.permission_gate = permission_gate
        self.state_store = state_store
        self.tracer = tracer
        self._queue: asyncio.Queue[Event] | None = None
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        if self._task:
            return
        self._queue = asyncio.Queue()
        self._unsubscribe = self.bus.subscribe_all(self._enqueue_event)
        self._task = asyncio.create_task(self._run_loop(), name="tool-executor")
        logger.info("tool executor started", extra={"run_id": "system"})

    async def shutdown(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._queue = None
        logger.info("tool executor stopped", extra={"run_id": "system"})

    async def _enqueue_event(self, event: Event) -> None:
        if event.type == "tool.requested" and self._queue is not None:
            await self._queue.put(event)

    async def _run_loop(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            event = await queue.get()
            await self._process_tool_request(event)

    async def _process_tool_request(self, event: Event) -> None:
        run_id = event.run_id
        data = event.data or {}
        tool_name = data.get("tool_name")
        arguments = data.get("arguments")
        parent_span_id = data.get("parent_span_id")
        normalized_name = tool_name.strip() if isinstance(tool_name, str) else "unknown"
        span_id = self._start_tool_span(run_id, normalized_name, parent_span_id)

        if not isinstance(tool_name, str) or not tool_name.strip():
            resolved_name = tool_name if isinstance(tool_name, str) and tool_name else "unknown"
            await self._emit_failure(
                run_id,
                resolved_name,
                {"error": "invalid_tool_name"},
                duration_ms=0,
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "schema_violation", "reason": "invalid_tool_name"},
            )
            return

        tool_name = tool_name.strip()

        if not isinstance(arguments, Mapping):
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "invalid_arguments"},
                duration_ms=0,
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "schema_violation", "reason": "invalid_arguments"},
            )
            return

        descriptor = self.registry.get_tool(tool_name)
        if not descriptor:
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "unknown_tool"},
                duration_ms=0,
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "schema_violation", "reason": "unknown_tool"},
            )
            return

        self._annotate_tool_span(
            run_id,
            span_id,
            {
                "tool_name": descriptor.name,
                "source": descriptor.source,
                "permission_scope": descriptor.permission_scope,
            },
        )
        permission_context = self._permission_context_for_run(run_id)
        allowed, reason = self.permission_gate.is_allowed(
            descriptor.permission_scope, permission_context
        )
        if not allowed:
            await self._emit_denied(
                run_id,
                tool_name,
                descriptor.permission_scope,
                reason or "permission_denied",
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "permission_denied", "reason": reason or "permission_denied"},
            )
            return

        start = time.perf_counter()
        try:
            result = await self.client.execute_tool(tool_name, arguments)
        except MCPServerError as exc:
            await self.bus.publish(
                tool_server_error_event(
                    run_id,
                    server_id=descriptor.server_id,
                    error=exc.details or {"error": str(exc)},
                )
            )
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "server_error"},
                duration_ms=self._duration_ms(start),
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {
                    "error_type": "network_failure",
                    "server_id": descriptor.server_id,
                },
            )
            logger.warning(
                "tool server error tool=%s server=%s", tool_name, descriptor.server_id, extra={"run_id": run_id}
            )
            return
        except Exception:  # pragma: no cover - defensive guard
            logger.exception(
                "tool execution crashed tool=%s", tool_name, extra={"run_id": run_id}
            )
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "execution_error"},
                duration_ms=self._duration_ms(start),
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "network_failure", "reason": "execution_error"},
            )
            return

        if result.error:
            await self._emit_failure(
                run_id,
                tool_name,
                result.error,
                duration_ms=self._duration_ms(start),
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "schema_violation", "reason": "tool_error"},
            )
            return

        await self._emit_success(
            run_id,
            tool_name,
            result.output or {},
            duration_ms=self._duration_ms(start),
        )
        self._end_tool_span(run_id, span_id, "success")

    async def _emit_success(
        self,
        run_id: str,
        tool_name: str,
        output: Mapping[str, object],
        *,
        duration_ms: int,
    ) -> None:
        await self.bus.publish(
            tool_completed_event(
                run_id, tool_name=tool_name, output=dict(output), duration_ms=duration_ms
            )
        )
        logger.info(
            "tool completed tool=%s duration_ms=%s",
            tool_name,
            duration_ms,
            extra={"run_id": run_id},
        )

    async def _emit_failure(
        self,
        run_id: str,
        tool_name: str,
        error: Mapping[str, object],
        *,
        duration_ms: int,
    ) -> None:
        await self.bus.publish(
            tool_failed_event(
                run_id, tool_name=tool_name, error=dict(error), duration_ms=duration_ms
            )
        )
        logger.info(
            "tool failed tool=%s duration_ms=%s",
            tool_name,
            duration_ms,
            extra={"run_id": run_id},
        )

    async def _emit_denied(
        self, run_id: str, tool_name: str, permission_scope: str, reason: str
    ) -> None:
        await self.bus.publish(
            tool_denied_event(
                run_id,
                tool_name=tool_name,
                permission_scope=permission_scope,
                reason=reason,
            )
        )
        logger.warning(
            "tool denied tool=%s scope=%s reason=%s",
            tool_name,
            permission_scope,
            reason,
            extra={"run_id": run_id},
        )

    def _permission_context_for_run(self, run_id: str):
        state = self.state_store.load(run_id)
        run_type = state.mode.value if state else "answer"
        return self.permission_gate.build_context(user_role="human", run_type=run_type)

    @staticmethod
    def _duration_ms(start: float) -> int:
        return max(int((time.perf_counter() - start) * 1000), 0)

    def _start_tool_span(
        self,
        run_id: str,
        tool_name: str,
        parent_span_id: str | None,
    ) -> str | None:
        if not self.tracer:
            return None
        return self.tracer.start_span(
            run_id,
            f"tool.{tool_name}",
            "tool",
            parent_span_id=parent_span_id,
            attributes={"tool_name": tool_name},
        )

    def _annotate_tool_span(
        self,
        run_id: str,
        span_id: str | None,
        attributes: Mapping[str, object],
    ) -> None:
        if not self.tracer or not span_id:
            return
        for key, value in attributes.items():
            self.tracer.add_span_attribute(run_id, span_id, key, value)

    def _end_tool_span(
        self,
        run_id: str,
        span_id: str | None,
        status: str,
        error: dict[str, object] | None = None,
    ) -> None:
        if not self.tracer or not span_id:
            return
        if error and error.get("error_type"):
            self.tracer.add_span_attribute(run_id, span_id, "error_type", error["error_type"])
        self.tracer.end_span(run_id, span_id, status, error)
