"""Cross-process lease abstraction used to prevent double-processing.

`single_process` mode uses a no-op implementation.
`distributed` mode should use a durable lease backed by shared infrastructure.
"""

from __future__ import annotations

from typing import Protocol


class RunLease(Protocol):
    async def acquire(self, key: str) -> bool:
        """Attempt to acquire the lease for `key`."""

    async def refresh(self, key: str) -> bool:
        """Refresh the lease for `key` if currently owned."""

    async def release(self, key: str) -> None:
        """Release the lease for `key` if currently owned."""

    async def close(self) -> None:
        """Release resources held by the lease provider."""


class NoopRunLease:
    """Lease provider for single-process mode."""

    async def acquire(self, key: str) -> bool:  # noqa: ARG002
        return True

    async def refresh(self, key: str) -> bool:  # noqa: ARG002
        return True

    async def release(self, key: str) -> None:  # noqa: ARG002
        return None

    async def close(self) -> None:
        return None

