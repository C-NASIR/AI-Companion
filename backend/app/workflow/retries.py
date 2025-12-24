"""Retry policy configuration for workflow activities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Retry configuration for a workflow step."""

    max_attempts: int
    backoff_seconds: int = 0

    def allows(self, attempt_number: int) -> bool:
        """Return True if another attempt is allowed after attempt_number."""
        return attempt_number < self.max_attempts


STEP_RETRY_RULES: dict[str, RetryPolicy] = {
    "receive": RetryPolicy(max_attempts=1),
    "plan": RetryPolicy(max_attempts=2, backoff_seconds=2),
    "retrieve": RetryPolicy(max_attempts=3, backoff_seconds=5),
    "respond": RetryPolicy(max_attempts=3, backoff_seconds=5),
    "verify": RetryPolicy(max_attempts=2, backoff_seconds=2),
    "maybe_approve": RetryPolicy(max_attempts=1),
    "finalize": RetryPolicy(max_attempts=1),
}


def policy_for_step(step: str) -> RetryPolicy:
    """Return the retry policy for the given workflow step."""
    return STEP_RETRY_RULES.get(step, RetryPolicy(max_attempts=1))
