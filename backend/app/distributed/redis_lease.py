"""Redis-backed lease provider for distributed mode.

Uses a simple value-based lease with TTL:
- acquire: SET key owner NX PX ttl
- refresh: if GET==owner then PEXPIRE
- release: if GET==owner then DEL
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class RedisLeaseConfig:
    url: str
    owner_id: str
    ttl_seconds: int
    key_prefix: str = "lease:run:"


class RedisRunLease:
    def __init__(self, config: RedisLeaseConfig):
        self._config = config
        self._client = None
        self._refresh_script = None
        self._release_script = None

    def _ttl_ms(self) -> int:
        return max(1, int(self._config.ttl_seconds * 1000))

    def _full_key(self, key: str) -> str:
        return f"{self._config.key_prefix}{key}"

    async def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            import redis.asyncio as redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            msg = (
                "Redis lease requested but redis client is unavailable. "
                "Install `redis` or run in BACKEND_MODE=single_process."
            )
            raise RuntimeError(msg) from exc

        self._client = redis.from_url(self._config.url, decode_responses=True)
        return self._client

    async def acquire(self, key: str) -> bool:
        client = await self._get_client()
        full_key = self._full_key(key)
        result = await client.set(
            full_key,
            self._config.owner_id,
            nx=True,
            px=self._ttl_ms(),
        )
        if result:
            return True

        # Re-entrant acquire: if we already own the lease, treat as acquired
        # and refresh the TTL.
        current = await client.get(full_key)
        if current != self._config.owner_id:
            return False
        await client.pexpire(full_key, self._ttl_ms())
        return True

    async def _ensure_scripts(self):
        if self._refresh_script is not None and self._release_script is not None:
            return
        client = await self._get_client()

        # refresh: if owned then pexpire
        refresh_lua = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "  return redis.call('PEXPIRE', KEYS[1], ARGV[2]) "
            "else return 0 end"
        )
        # release: delete only if owned
        release_lua = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "  return redis.call('DEL', KEYS[1]) "
            "else return 0 end"
        )
        self._refresh_script = client.register_script(refresh_lua)
        self._release_script = client.register_script(release_lua)

    async def refresh(self, key: str) -> bool:
        await self._ensure_scripts()
        assert self._refresh_script is not None
        full_key = self._full_key(key)
        result = await self._refresh_script(keys=[full_key], args=[self._config.owner_id, self._ttl_ms()])
        return int(result or 0) > 0

    async def release(self, key: str) -> None:
        await self._ensure_scripts()
        assert self._release_script is not None
        full_key = self._full_key(key)
        await self._release_script(keys=[full_key], args=[self._config.owner_id])

    async def close(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        try:
            await client.close()
        except TypeError:
            # Some redis client versions use sync close.
            await asyncio.to_thread(client.close)
