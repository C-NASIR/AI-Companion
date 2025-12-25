"""Budget tracking for per-run model spend."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class BudgetExceeded(Exception):
    """Raised when a run exceeds the configured budget."""

    spent_usd: float
    limit_usd: float

    @property
    def reason(self) -> str:
        return "budget_exhausted"


class BudgetManager:
    """Tracks per-run model spend against a static USD limit."""

    def __init__(self, limit_usd: float):
        self.limit_usd = max(float(limit_usd or 0.0), 0.0)
        self._spent: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, run_id: str, amount_usd: float) -> float:
        """Record additional spend and return the new total."""
        if amount_usd <= 0:
            return self._spent.get(run_id, 0.0)
        with self._lock:
            total = self._spent.get(run_id, 0.0) + amount_usd
            self._spent[run_id] = total
        if self.limit_usd and total > self.limit_usd:
            raise BudgetExceeded(spent_usd=total, limit_usd=self.limit_usd)
        return total

    def reset(self, run_id: str) -> None:
        with self._lock:
            self._spent.pop(run_id, None)
