"""Tool queue primitives.

In single-process mode tools are executed by subscribing to the in-memory event
bus. In distributed mode we want a durable queue so tool execution can be moved
to dedicated worker processes.
"""

from __future__ import annotations

from typing import Protocol


class ToolQueuePublisher(Protocol):
    async def enqueue_tool_requested(self, event_payload_json: str, *, run_id: str, event_id: str) -> None:
        """Enqueue a persisted `tool.requested` event for background execution."""


class NoopToolQueuePublisher:
    async def enqueue_tool_requested(self, event_payload_json: str, *, run_id: str, event_id: str) -> None:  # noqa: ARG002
        return None

