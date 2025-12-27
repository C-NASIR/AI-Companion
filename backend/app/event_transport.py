"""Event transport implementations.

The event store remains the durable source of truth. Transports provide
best-effort live fanout to subscribers (e.g. SSE clients, background workers).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from .events import Event
else:
    Event = Any

EventCallback = Callable[[Event], Awaitable[None]]


class InMemoryEventTransport:
    """Process-local pub/sub transport."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[EventCallback]] = {}
        self._global_subscribers: set[EventCallback] = set()

    async def publish(self, event: Event) -> None:
        callbacks = list(self._subscribers.get(event.run_id, ()))
        global_callbacks = list(self._global_subscribers)
        for callback in callbacks + global_callbacks:
            try:
                await callback(event)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "event subscriber failed run_id=%s type=%s",
                    event.run_id,
                    event.type,
                )

    def subscribe(self, run_id: str, callback: EventCallback) -> Callable[[], None]:
        subscribers = self._subscribers.setdefault(run_id, set())
        subscribers.add(callback)

        def _unsubscribe() -> None:
            current = self._subscribers.get(run_id)
            if not current:
                return
            current.discard(callback)
            if not current:
                self._subscribers.pop(run_id, None)

        return _unsubscribe

    def subscribe_all(self, callback: EventCallback) -> Callable[[], None]:
        self._global_subscribers.add(callback)

        def _unsubscribe() -> None:
            self._global_subscribers.discard(callback)

        return _unsubscribe

    async def close(self) -> None:
        self._subscribers.clear()
        self._global_subscribers.clear()


@dataclass(frozen=True)
class RedisEventTransportConfig:
    url: str
    channel_prefix: str = "events:"
    run_prefix: str = "run:"
    global_channel: str = "all"


class RedisEventTransport:
    """Redis Pub/Sub transport.

    This is intended for live fanout, not durability. Events remain persisted in
    the event store first.
    """

    def __init__(self, config: RedisEventTransportConfig) -> None:
        self._config = config
        self._client = None
        self._pubsub = None
        self._listener_task: asyncio.Task[None] | None = None
        self._subscribers: dict[str, set[EventCallback]] = {}
        self._global_subscribers: set[EventCallback] = set()
        self._subscribed_channels: set[str] = set()
        self._lock = asyncio.Lock()

    def _channel_for_run(self, run_id: str) -> str:
        return f"{self._config.channel_prefix}{self._config.run_prefix}{run_id}"

    def _global_channel(self) -> str:
        return f"{self._config.channel_prefix}{self._config.global_channel}"

    async def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            msg = (
                "Redis transport requested but redis client is unavailable. "
                "Install `redis` or run in BACKEND_MODE=single_process."
            )
            raise RuntimeError(msg) from exc
        self._client = redis.from_url(self._config.url, decode_responses=True)
        return self._client

    async def _ensure_listener(self) -> None:
        if self._listener_task is not None and not self._listener_task.done():
            return
        client = await self._get_client()
        self._pubsub = client.pubsub()
        self._listener_task = asyncio.create_task(self._listen_loop(), name="redis-event-transport")

    async def _subscribe_channel(self, channel: str) -> None:
        await self._ensure_listener()
        assert self._pubsub is not None
        if channel in self._subscribed_channels:
            return
        await self._pubsub.subscribe(channel)
        self._subscribed_channels.add(channel)

    async def _unsubscribe_channel(self, channel: str) -> None:
        if self._pubsub is None:
            return
        if channel not in self._subscribed_channels:
            return
        await self._pubsub.unsubscribe(channel)
        self._subscribed_channels.discard(channel)

    async def publish(self, event: Event) -> None:
        client = await self._get_client()
        payload = json.dumps(event.model_dump(), separators=(",", ":"))
        await client.publish(self._channel_for_run(event.run_id), payload)
        await client.publish(self._global_channel(), payload)

    def subscribe(self, run_id: str, callback: EventCallback) -> Callable[[], None]:
        subscribers = self._subscribers.setdefault(run_id, set())
        subscribers.add(callback)

        async def _ensure() -> None:
            async with self._lock:
                await self._subscribe_channel(self._channel_for_run(run_id))

        asyncio.create_task(_ensure())

        def _unsubscribe() -> None:
            current = self._subscribers.get(run_id)
            if not current:
                return
            current.discard(callback)
            if current:
                return
            self._subscribers.pop(run_id, None)

            async def _cleanup() -> None:
                async with self._lock:
                    await self._unsubscribe_channel(self._channel_for_run(run_id))

            asyncio.create_task(_cleanup())

        return _unsubscribe

    def subscribe_all(self, callback: EventCallback) -> Callable[[], None]:
        self._global_subscribers.add(callback)

        async def _ensure() -> None:
            async with self._lock:
                await self._subscribe_channel(self._global_channel())

        asyncio.create_task(_ensure())

        def _unsubscribe() -> None:
            self._global_subscribers.discard(callback)
            if self._global_subscribers:
                return

            async def _cleanup() -> None:
                async with self._lock:
                    await self._unsubscribe_channel(self._global_channel())

            asyncio.create_task(_cleanup())

        return _unsubscribe

    async def _listen_loop(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if not isinstance(message, dict):
                    continue
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not isinstance(data, str) or not data:
                    continue
                try:
                    from .events import Event as EventModel

                    event = EventModel.model_validate(json.loads(data))
                except Exception:
                    logger.warning("skipping malformed transport event")
                    continue

                callbacks = list(self._subscribers.get(event.run_id, ()))
                global_callbacks = list(self._global_subscribers)
                for callback in callbacks + global_callbacks:
                    try:
                        await callback(event)
                    except Exception:  # pragma: no cover - defensive logging
                        logger.exception(
                            "transport subscriber failed run_id=%s type=%s",
                            event.run_id,
                            event.type,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("redis transport listener crashed")

    async def close(self) -> None:
        async with self._lock:
            if self._listener_task is not None:
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    pass
                self._listener_task = None

            if self._pubsub is not None:
                try:
                    await self._pubsub.close()
                except Exception:
                    pass
                self._pubsub = None

            if self._client is not None:
                client = self._client
                self._client = None
                try:
                    await client.close()
                except Exception:
                    pass

            self._subscribers.clear()
            self._global_subscribers.clear()
            self._subscribed_channels.clear()
