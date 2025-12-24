"""Utility helpers for intelligence modules."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def log_run(run_id: str, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})
