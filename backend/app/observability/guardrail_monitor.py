"""Simple in-process monitor that aggregates guardrail signals."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any, Callable

from ..events import Event, EventBus

logger = logging.getLogger(__name__)


class GuardrailMonitor:
    """Aggregates guardrail events for dashboards/alerts."""

    def __init__(
        self,
        bus: EventBus,
        *,
        report_interval: int = 120,
        subscribe: bool = True,
    ) -> None:
        self.bus = bus
        self.report_interval = max(report_interval, 30)
        self._unsubscribe: Callable[[], None] | None = None
        if subscribe:
            self._unsubscribe = self.bus.subscribe_all(self._handle_event)
        self._guardrail_counts: Counter[str] = Counter()
        self._sanitization_counts: Counter[str] = Counter()
        self._injection_counts: Counter[str] = Counter()
        self._last_report = time.monotonic()
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Begin consuming events from the bus (idempotent)."""
        if self._unsubscribe:
            return
        self._unsubscribe = self.bus.subscribe_all(self._handle_event)

    async def _handle_event(self, event: Event) -> None:
        """Collect metrics and periodically log summaries."""
        async with self._lock:
            if event.type == "guardrail.triggered":
                layer = str(event.data.get("layer") or "unknown")
                threat = str(event.data.get("threat_type") or "unknown")
                key = f"{layer}:{threat}"
                self._guardrail_counts[key] += 1
            elif event.type == "context.sanitized":
                chunk_id = str(event.data.get("original_chunk_id") or "unknown")
                if event.data.get("sanitization_applied"):
                    self._sanitization_counts[chunk_id] += 1
            elif event.type == "injection.detected":
                location = str(event.data.get("location") or "unknown")
                self._injection_counts[location] += 1

            now = time.monotonic()
            if now - self._last_report >= self.report_interval:
                self._emit_report()
                self._last_report = now

    def _emit_report(self) -> None:
        if not (self._guardrail_counts or self._sanitization_counts or self._injection_counts):
            return
        summary: dict[str, Any] = {}
        if self._guardrail_counts:
            summary["guardrail_counts"] = dict(self._guardrail_counts.most_common())
        if self._sanitization_counts:
            summary["sanitized_chunks"] = len(self._sanitization_counts)
        if self._injection_counts:
            summary["injection_locations"] = dict(self._injection_counts.most_common())
        logger.info(
            "guardrail.monitor report=%s",
            summary,
            extra={"run_id": "system"},
        )

    def close(self) -> None:
        """Unsubscribe from the event bus."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
