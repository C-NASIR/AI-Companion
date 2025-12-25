"""Simple concurrency rate limiter for runs."""

from __future__ import annotations

import threading
from collections import defaultdict


class RateLimiter:
    """Tracks active runs per tenant and globally."""

    def __init__(self, global_limit: int, tenant_limit: int):
        self.global_limit = max(global_limit, 0)
        self.tenant_limit = max(tenant_limit, 0)
        self._active: dict[str, str] = {}
        self._tenant_counts: defaultdict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def try_acquire(self, run_id: str, tenant_id: str) -> bool:
        tenant = tenant_id or "default"
        with self._lock:
            if self.global_limit and len(self._active) >= self.global_limit:
                return False
            if self.tenant_limit and self._tenant_counts[tenant] >= self.tenant_limit:
                return False
            self._active[run_id] = tenant
            self._tenant_counts[tenant] += 1
            return True

    def release(self, run_id: str) -> None:
        with self._lock:
            tenant = self._active.pop(run_id, None)
            if tenant is None:
                return
            self._tenant_counts[tenant] -= 1
            if self._tenant_counts[tenant] <= 0:
                self._tenant_counts.pop(tenant, None)
