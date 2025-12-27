"""Tool execution worker for distributed mode.

Consumes durable `tool.requested` events from Redis Streams and executes them
via `ToolExecutor`, emitting completion/failure events.
"""

from __future__ import annotations

import asyncio
import logging
import os
from uuid import uuid4

from ..container import build_container, shutdown as shutdown_container, startup as startup_container
from ..env import load_dotenv_if_present
from ..events import Event
from ..executor import ToolExecutor
from ..mcp.bootstrap import initialize_mcp
from ..settings import get_settings
from ..distributed.redis_tool_queue import RedisToolQueue, RedisToolQueueConfig

logger = logging.getLogger(__name__)


def _consumer_name() -> str:
    return (
        os.getenv("TOOL_QUEUE_CONSUMER_NAME")
        or os.getenv("HOSTNAME")
        or f"tool-worker-{uuid4()}"
    )


async def run_tool_worker() -> None:
    load_dotenv_if_present()
    settings = get_settings()
    if settings.runtime.mode != "distributed":
        raise RuntimeError("tool worker requires BACKEND_MODE=distributed")
    if not settings.runtime.redis_url:
        raise RuntimeError("tool worker requires REDIS_URL")

    container = build_container(settings=settings)
    # Only perform filesystem prep; do not start RunCoordinator subscriptions in tool workers.
    startup_container(container, start_coordinator=False, start_guardrail_monitor=False)
    await initialize_mcp(container)

    tool_executor = ToolExecutor(
        container.event_bus,
        container.mcp_registry,
        container.mcp_client,
        container.permission_gate,
        container.state_store,
        container.tracer,
        tool_firewall_enabled=settings.guardrails.tool_firewall_enabled,
        cache_store=container.cache_store,
        tool_cache_enabled=settings.caching.tool_cache_enabled,
    )

    queue = RedisToolQueue(
        RedisToolQueueConfig(
            url=settings.runtime.redis_url,
            consumer_name=_consumer_name(),
        )
    )

    async def _handle(raw_event: dict) -> None:
        event = Event.model_validate(raw_event)
        await tool_executor.process_tool_requested(event)

    logger.info(
        "tool worker started consumer=%s stream=%s group=%s",
        queue._config.consumer_name,
        queue._config.stream_key,
        queue._config.group_name,
        extra={"run_id": "system"},
    )
    try:
        await queue.run_consumer(_handle)
    finally:
        await queue.close()
        await shutdown_container(container)


def main() -> None:
    asyncio.run(run_tool_worker())


if __name__ == "__main__":  # pragma: no cover
    main()

