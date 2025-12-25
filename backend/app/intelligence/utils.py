"""Utility helpers for intelligence modules."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


from ..state_store import StateStore

_STATE_STORE: StateStore | None = None


def configure_state_store(store: StateStore) -> None:
    global _STATE_STORE
    _STATE_STORE = store


def log_run(run_id: str, message: str, *args: object) -> None:
    extra = {"run_id": run_id}
    if _STATE_STORE:
        state = _STATE_STORE.load(run_id)
        if state:
            extra["tenant_id"] = state.tenant_id
            extra["user_id"] = state.user_id
    logger.info(message, *args, extra=extra)
