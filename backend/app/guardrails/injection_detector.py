"""Prompt injection signal detector."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing_extensions import Literal

from ..events import injection_detected_event
from .base import EventPublisher
from .threats import ThreatConfidence


@dataclass(frozen=True)
class InjectionPattern:
    """Describes a detection pattern and associated confidence."""

    name: str
    pattern: re.Pattern[str]
    confidence: ThreatConfidence


class InjectionDetector:
    """Emits observability signals for suspicious content."""

    def __init__(self, publisher: EventPublisher):
        self.publisher = publisher
        self._patterns: list[InjectionPattern] = [
            InjectionPattern(
                "role_confusion",
                re.compile(r"(?i)you are (?:now|currently) the system"),
                ThreatConfidence.MEDIUM,
            ),
            InjectionPattern(
                "instruction_override",
                re.compile(r"(?i)ignore (?:all\s+|the\s+)?previous instructions"),
                ThreatConfidence.HIGH,
            ),
            InjectionPattern(
                "hidden_tool_request",
                re.compile(r"(?i)call tool\.|{{tool:"),
                ThreatConfidence.LOW,
            ),
            InjectionPattern(
                "boundary_adjustment",
                re.compile(r"(?i)change the system boundaries"),
                ThreatConfidence.LOW,
            ),
        ]

    async def scan(
        self,
        run_id: str,
        text: str,
        location: Literal["input", "retrieval", "output"],
    ) -> list[str]:
        """Scan text and emit injection.detected events for matches."""
        matches: list[str] = []
        payload = text or ""
        for descriptor in self._patterns:
            if descriptor.pattern.search(payload):
                matches.append(descriptor.name)
                await self.publisher.publish(
                    injection_detected_event(
                        run_id,
                        location=location,
                        confidence=descriptor.confidence,
                        pattern=descriptor.name,
                    )
                )
        return matches
