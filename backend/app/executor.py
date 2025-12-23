"""Tool executor service: subscribes to tool.requested and emits results."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Mapping

from pydantic import ValidationError

from .events import (
    Event,
    EventBus,
    tool_completed_event,
    tool_failed_event,
)
from .tools import (
    ToolExecutionError,
    ToolRegistry,
    ToolErrorModel,
    ToolOutputModel,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes registered tools in response to tool.requested events."""

    def __init__(self, bus: EventBus, registry: ToolRegistry):
        self.bus = bus
        self.registry = registry
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

        if not isinstance(tool_name, str) or not tool_name.strip():
            resolved_name = tool_name if isinstance(tool_name, str) and tool_name else "unknown"
            await self._emit_failure(
                run_id,
                resolved_name,
                {"error": "invalid_tool_name"},
                duration_ms=0,
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
            return

        spec = self.registry.get(tool_name)
        if not spec:
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "unknown_tool"},
                duration_ms=0,
            )
            return

        start = time.perf_counter()

        try:
            validated_args = validate_tool_arguments(spec, arguments)
        except ValidationError as exc:
            message = "invalid_arguments"
            logger.warning(
                "tool arg validation failed tool=%s errors=%s",
                tool_name,
                exc.errors(),
                extra={"run_id": run_id},
            )
            await self._emit_failure(
                run_id,
                tool_name,
                spec.error_model(error=message),
                duration_ms=self._duration_ms(start),
            )
            return

        try:
            output = spec.execute(validated_args)
            if not isinstance(output, ToolOutputModel):
                output = spec.output_model.model_validate(  # type: ignore[arg-type]
                    output
                )
        except ToolExecutionError as exc:
            await self._emit_failure(
                run_id,
                tool_name,
                spec.error_model.model_validate(exc.error_payload.model_dump()),
                duration_ms=self._duration_ms(start),
            )
            return
        except Exception:
            logger.exception(
                "tool execution crashed tool=%s", tool_name, extra={"run_id": run_id}
            )
            await self._emit_failure(
                run_id,
                tool_name,
                spec.error_model(error="execution_error"),
                duration_ms=self._duration_ms(start),
            )
            return

        await self._emit_success(
            run_id,
            tool_name,
            output,
            duration_ms=self._duration_ms(start),
        )

    async def _emit_success(
        self,
        run_id: str,
        tool_name: str,
        output: ToolOutputModel,
        *,
        duration_ms: int,
    ) -> None:
        payload = output.model_dump()
        await self.bus.publish(
            tool_completed_event(
                run_id, tool_name=tool_name, output=payload, duration_ms=duration_ms
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
        error: ToolErrorModel | Mapping[str, object],
        *,
        duration_ms: int,
    ) -> None:
        if isinstance(error, ToolErrorModel):
            error_payload = error.model_dump()
        else:
            error_payload = dict(error)
        await self.bus.publish(
            tool_failed_event(
                run_id, tool_name=tool_name, error=error_payload, duration_ms=duration_ms
            )
        )
        logger.info(
            "tool failed tool=%s duration_ms=%s",
            tool_name,
            duration_ms,
            extra={"run_id": run_id},
        )

    @staticmethod
    def _duration_ms(start: float) -> int:
        return max(int((time.perf_counter() - start) * 1000), 0)
