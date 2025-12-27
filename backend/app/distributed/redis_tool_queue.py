"""Redis Streams-backed queue for tool execution.

Producer:
- Append `tool.requested` events to a Redis Stream.

Consumer:
- A worker reads from the stream via consumer group and processes events.

This provides durability (unlike Pub/Sub). Idempotency is still recommended.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedisToolQueueConfig:
    url: str
    stream_key: str = "queue:tools"
    group_name: str = "tool-workers"
    consumer_name: str = "worker-1"
    block_ms: int = 5000
    idle_sleep_seconds: float = 0.1
    idempotency_key_prefix: str = "tool:processed:"
    idempotency_ttl_seconds: int = 86400


class RedisToolQueue:
    def __init__(self, config: RedisToolQueueConfig) -> None:
        self._config = config
        self._client = None

    async def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            msg = (
                "Redis tool queue requested but redis client is unavailable. "
                "Install `redis` or run in BACKEND_MODE=single_process."
            )
            raise RuntimeError(msg) from exc
        self._client = redis.from_url(self._config.url, decode_responses=True)
        return self._client

    async def ensure_group(self) -> None:
        client = await self._get_client()
        try:
            await client.xgroup_create(
                name=self._config.stream_key,
                groupname=self._config.group_name,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            # BUSYGROUP (group exists) or other errors.
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue_tool_requested(self, event_payload_json: str, *, run_id: str, event_id: str) -> None:
        client = await self._get_client()
        await client.xadd(
            name=self._config.stream_key,
            fields={"event": event_payload_json, "run_id": run_id, "event_id": event_id},
        )

    async def _mark_processed(self, event_id: str) -> bool:
        client = await self._get_client()
        key = f"{self._config.idempotency_key_prefix}{event_id}"
        return bool(
            await client.set(
                key,
                "1",
                nx=True,
                ex=max(60, int(self._config.idempotency_ttl_seconds)),
            )
        )

    async def run_consumer(self, handler) -> None:
        """Consume tool requests forever.

        `handler(event_dict)` should process the tool.requested event.
        """

        await self.ensure_group()
        client = await self._get_client()
        stream = self._config.stream_key
        group = self._config.group_name
        consumer = self._config.consumer_name

        while True:
            try:
                entries = await client.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream: ">"},
                    count=10,
                    block=self._config.block_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("tool queue read failed")
                await asyncio.sleep(self._config.idle_sleep_seconds)
                continue

            if not entries:
                await asyncio.sleep(self._config.idle_sleep_seconds)
                continue

            for _stream_name, messages in entries:
                for message_id, fields in messages:
                    try:
                        payload_json = fields.get("event")
                        if not isinstance(payload_json, str) or not payload_json:
                            await client.xack(stream, group, message_id)
                            continue
                        raw = json.loads(payload_json)
                        event_id = raw.get("id")
                        if not isinstance(event_id, str) or not event_id:
                            await client.xack(stream, group, message_id)
                            continue

                        fresh = await self._mark_processed(event_id)
                        if not fresh:
                            await client.xack(stream, group, message_id)
                            continue

                        await handler(raw)
                        await client.xack(stream, group, message_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("tool queue handler failed")
                        # Don't ack; let another worker retry.

    async def close(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        try:
            await client.close()
        except Exception:
            try:
                await asyncio.to_thread(client.close)
            except Exception:
                return

