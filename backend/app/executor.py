"""Tool executor that routes requests through the MCP client."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Callable, Mapping

from .cache import CacheStore
from .events import (
    Event,
    EventBus,
    cache_hit_event,
    cache_miss_event,
    guardrail_triggered_event,
    tool_completed_event,
    tool_denied_event,
    tool_failed_event,
    tool_server_error_event,
)
from .guardrails.threats import ThreatAssessment, ThreatConfidence, ThreatType
from .mcp.client import MCPClient
from .mcp.registry import MCPRegistry
from .mcp.server import MCPServerError
from .observability.tracer import Tracer
from .permissions import PermissionGate
from .state_store import StateStore
from .lease import RunLease

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
        *,
        run_lease: RunLease | None = None,
        lease_key: str = "system:tool_executor",
        tool_firewall_enabled: bool = True,
        cache_store: CacheStore | None = None,
        tool_cache_enabled: bool = True,
    ):
        self.bus = bus
        self.registry = registry
        self.client = client
        self.permission_gate = permission_gate
        self.state_store = state_store
        self.tracer = tracer
        self.run_lease = run_lease
        self.lease_key = lease_key
        self._lease_acquired = False
        self._queue: asyncio.Queue[Event] | None = None
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._max_tools_per_run = 3
        self._tool_firewall_enabled = tool_firewall_enabled
        self.cache_store = cache_store
        self.tool_cache_enabled = tool_cache_enabled

    async def start(self) -> None:
        if self._task:
            return

        if self.run_lease is not None:
            self._lease_acquired = await self.run_lease.acquire(self.lease_key)
            if not self._lease_acquired:
                logger.info(
                    "tool executor lease unavailable; not starting",
                    extra={"run_id": "system"},
                )
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
        if self._lease_acquired and self.run_lease is not None:
            await self.run_lease.release(self.lease_key)
        self._lease_acquired = False

    async def _enqueue_event(self, event: Event) -> None:
        if event.type == "tool.requested" and self._queue is not None:
            await self._queue.put(event)
        if event.type in {"run.completed", "run.failed"}:
            self._tool_counts.pop(event.run_id, None)

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

        state = self.state_store.load(run_id)
        log_extra = state.log_extra() if state else {"run_id": run_id}
        tenant_id = state.tenant_id if state else "default"
        user_id = state.user_id if state else "anonymous"
        identity = {"tenant_id": tenant_id, "user_id": user_id}

        if not isinstance(tool_name, str) or not tool_name.strip():
            resolved_name = tool_name if isinstance(tool_name, str) and tool_name else "unknown"
            await self._emit_failure(
                run_id,
                resolved_name,
                {"error": "invalid_tool_name"},
                duration_ms=0,
                identity=identity,
                log_extra=log_extra,
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
                identity=identity,
                log_extra=log_extra,
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
                identity=identity,
                log_extra=log_extra,
            )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "schema_violation", "reason": "unknown_tool"},
            )
            return

        if self._tool_firewall_enabled and state and state.available_tools:
            allowed = {entry.name for entry in state.available_tools}
            if tool_name not in allowed:
                await self._deny_for_guardrail(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    "tool_not_allowlisted",
                    identity=identity,
                    log_extra=log_extra,
                )
                self._end_tool_span(
                    run_id,
                    span_id,
                    "failed",
                    {
                        "error_type": "guardrail_failure",
                        "reason": "tool_not_allowlisted",
                    },
                )
                return

        if self._tool_firewall_enabled:
            valid_args, arg_reason = self._validate_arguments(
                descriptor.input_schema,
                arguments,
            )
            if not valid_args:
                await self._deny_for_guardrail(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    arg_reason,
                    identity=identity,
                    log_extra=log_extra,
                )
                self._end_tool_span(
                    run_id,
                    span_id,
                    "failed",
                    {
                        "error_type": "guardrail_failure",
                        "reason": arg_reason,
                    },
                )
                return

            if self._tool_counts[run_id] >= self._max_tools_per_run:
                await self._deny_for_guardrail(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    "tool_rate_limit_exceeded",
                    identity=identity,
                    log_extra=log_extra,
                )
                self._end_tool_span(
                    run_id,
                    span_id,
                    "failed",
                    {
                        "error_type": "guardrail_failure",
                        "reason": "tool_rate_limit_exceeded",
                    },
                )
                return

        self._tool_counts[run_id] += 1

        side_effect = self._classify_side_effect(descriptor.permission_scope)
        permission_context = self._permission_context_for_run(run_id)
        allowed, reason = self.permission_gate.is_allowed(
            descriptor.permission_scope, permission_context
        )
        if not allowed:
            if self._tool_firewall_enabled:
                await self._deny_for_guardrail(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    reason or "permission_denied",
                    identity=identity,
                    log_extra=log_extra,
                )
            else:
                await self._emit_denied(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    reason or "permission_denied",
                    identity=identity,
                    log_extra=log_extra,
                )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "permission_denied", "reason": reason or "permission_denied"},
            )
            return

        if descriptor.permission_scope == "github.read" and not self._has_github_credentials():
            reason = "missing_github_token"
            if self._tool_firewall_enabled:
                await self._deny_for_guardrail(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    reason,
                    identity=identity,
                    log_extra=log_extra,
                )
            else:
                await self._emit_denied(
                    run_id,
                    tool_name,
                    descriptor.permission_scope,
                    reason,
                    identity=identity,
                    log_extra=log_extra,
                )
            self._end_tool_span(
                run_id,
                span_id,
                "failed",
                {"error_type": "permission_denied", "reason": reason},
            )
            return

        cache_status = "disabled"
        cached_output: Mapping[str, object] | None = None
        cache_key: str | None = None
        cache_metadata = {
            "tool_name": tool_name,
            "permission_scope": descriptor.permission_scope,
            "source": descriptor.source,
            "tenant_id": tenant_id,
        }
        cacheable = (
            self.cache_store is not None
            and self.tool_cache_enabled
            and side_effect == "read"
        )
        if cacheable:
            cache_key, cached_output = self.cache_store.tool_lookup(
                tenant_id, tool_name, arguments
            )
            if cached_output is not None:
                cache_status = "hit"
                await self.bus.publish(
                    cache_hit_event(
                        run_id,
                        cache_name="tool_result",
                        key=cache_key,
                        metadata=cache_metadata,
                        identity=identity,
                    )
                )
            else:
                cache_status = "miss"
                await self.bus.publish(
                    cache_miss_event(
                        run_id,
                        cache_name="tool_result",
                        key=cache_key,
                        metadata=cache_metadata,
                        identity=identity,
                    )
                )
        self._annotate_tool_span(
            run_id,
            span_id,
            {
                "tool_name": descriptor.name,
                "source": descriptor.source,
                "permission_scope": descriptor.permission_scope,
                "side_effect": side_effect,
                "cache_status": cache_status,
            },
        )
        if cached_output is not None:
            await self._emit_success(
                run_id,
                tool_name,
                cached_output,
                duration_ms=0,
                identity=identity,
                log_extra=log_extra,
            )
            self._end_tool_span(run_id, span_id, "success")
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
                    identity=identity,
                )
            )
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "server_error"},
                duration_ms=self._duration_ms(start),
                identity=identity,
                log_extra=log_extra,
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
                "tool server error tool=%s server=%s",
                tool_name,
                descriptor.server_id,
                extra=log_extra,
            )
            return
        except Exception:  # pragma: no cover - defensive guard
            logger.exception(
                "tool execution crashed tool=%s",
                tool_name,
                extra=log_extra,
            )
            await self._emit_failure(
                run_id,
                tool_name,
                {"error": "execution_error"},
                duration_ms=self._duration_ms(start),
                identity=identity,
                log_extra=log_extra,
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
                identity=identity,
                log_extra=log_extra,
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
            identity=identity,
            log_extra=log_extra,
        )
        if cacheable and cache_key:
            self.cache_store.store_tool(tenant_id, tool_name, arguments, result.output or {})
        self._end_tool_span(run_id, span_id, "success")

    async def process_tool_requested(self, event: Event) -> None:
        """Process a persisted `tool.requested` event.

        Used by distributed tool workers that consume from a durable queue.
        """

        if event.type != "tool.requested":
            return
        await self._process_tool_request(event)

    async def _emit_success(
        self,
        run_id: str,
        tool_name: str,
        output: Mapping[str, object],
        *,
        duration_ms: int,
        identity: Mapping[str, Any],
        log_extra: Mapping[str, str],
    ) -> None:
        await self.bus.publish(
            tool_completed_event(
                run_id,
                tool_name=tool_name,
                output=dict(output),
                duration_ms=duration_ms,
                identity=identity,
            )
        )
        logger.info(
            "tool completed tool=%s duration_ms=%s",
            tool_name,
            duration_ms,
            extra=log_extra,
        )

    async def _emit_failure(
        self,
        run_id: str,
        tool_name: str,
        error: Mapping[str, object],
        *,
        duration_ms: int,
        identity: Mapping[str, Any],
        log_extra: Mapping[str, str],
    ) -> None:
        await self.bus.publish(
            tool_failed_event(
                run_id,
                tool_name=tool_name,
                error=dict(error),
                duration_ms=duration_ms,
                identity=identity,
            )
        )
        logger.info(
            "tool failed tool=%s duration_ms=%s",
            tool_name,
            duration_ms,
            extra=log_extra,
        )

    async def _emit_denied(
        self,
        run_id: str,
        tool_name: str,
        permission_scope: str,
        reason: str,
        *,
        identity: Mapping[str, Any],
        log_extra: Mapping[str, str],
    ) -> None:
        await self.bus.publish(
            tool_denied_event(
                run_id,
                tool_name=tool_name,
                permission_scope=permission_scope,
                reason=reason,
                identity=identity,
            )
        )
        logger.warning(
            "tool denied tool=%s scope=%s reason=%s",
            tool_name,
            permission_scope,
            reason,
            extra=log_extra,
        )

    def _permission_context_for_run(self, run_id: str):
        state = self.state_store.load(run_id)
        run_type = state.mode.value if state else "answer"
        is_evaluation = bool(state.is_evaluation) if state else False
        return self.permission_gate.build_context(
            user_role="human",
            run_type=run_type,
            is_evaluation=is_evaluation,
        )

    @staticmethod
    def _has_github_credentials() -> bool:
        return bool(
            os.getenv("GITHUB_TOKEN")
            or os.getenv("GITHUB_PAT")
            or os.getenv("GITHUB_API_TOKEN")
        )

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

    async def _deny_for_guardrail(
        self,
        run_id: str,
        tool_name: str,
        permission_scope: str,
        reason: str,
        *,
        identity: Mapping[str, Any],
        log_extra: Mapping[str, str],
    ) -> None:
        assessment = ThreatAssessment(
            threat_type=ThreatType.TOOL_ABUSE,
            confidence=ThreatConfidence.MEDIUM,
            notes=reason,
        )
        await self.bus.publish(
            guardrail_triggered_event(
                run_id,
                layer="tool",
                assessment=assessment,
                identity=identity,
            )
        )
        await self._emit_denied(
            run_id,
            tool_name,
            permission_scope,
            reason,
            identity=identity,
            log_extra=log_extra,
        )

    @staticmethod
    def _classify_side_effect(permission_scope: str) -> str:
        lowered = (permission_scope or "").lower()
        if any(token in lowered for token in ("write", "modify", "delete", "admin")):
            return "write"
        return "read"

    @staticmethod
    def _validate_arguments(
        schema: Mapping[str, object] | None, arguments: Mapping[str, object]
    ) -> tuple[bool, str]:
        if not schema:
            return True, ""
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        required = schema.get("required") if isinstance(schema, Mapping) else None
        missing: list[str] = []
        if isinstance(required, list):
            for field in required:
                if field not in arguments:
                    missing.append(str(field))
        if missing:
            return False, f"missing arguments: {', '.join(missing)}"
        if isinstance(properties, Mapping) and properties:
            unexpected = [key for key in arguments if key not in properties]
            if unexpected:
                return False, f"unexpected arguments: {', '.join(unexpected)}"
            for name, constraint in properties.items():
                if name not in arguments or not isinstance(constraint, Mapping):
                    continue
                expected_type = constraint.get("type")
                if expected_type and not ToolExecutor._argument_matches_type(
                    arguments[name], expected_type
                ):
                    return False, f"invalid type for argument {name}"
        return True, ""

    @staticmethod
    def _argument_matches_type(value: object, schema_type: object) -> bool:
        if not isinstance(schema_type, str):
            return True
        mapping = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": Mapping,
            "array": (list, tuple),
        }
        expected = mapping.get(schema_type)
        if not expected:
            return True
        return isinstance(value, expected)
