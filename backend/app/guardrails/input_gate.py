"""Input gate implementation that blocks unsafe user requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable
from typing_extensions import Literal

from ..events import guardrail_triggered_event
from ..schemas import ChatMode
from .base import EventPublisher, GuardrailViolation
from .threats import ThreatAssessment, ThreatConfidence, ThreatType


Matcher = Callable[[str], str | None]


@dataclass(frozen=True)
class GuardrailRule:
    """Declarative guardrail rule evaluated against the raw input."""

    name: str
    threat_type: ThreatType
    confidence: ThreatConfidence
    matcher: Matcher
    category: Literal["override", "action", "leak", "payload"]


class InputGate:
    """Performs synchronous checks on the initial user message."""

    def __init__(self, publisher: EventPublisher):
        self.publisher = publisher
        self._rules = self._build_rules()

    async def enforce(self, run_id: str, user_input: str, mode: ChatMode | None = None) -> None:
        """Validate user input and raise if it violates safety policy."""
        text = (user_input or "").strip()
        if not text:
            return
        lowered = text.lower()
        for rule in self._rules:
            notes = rule.matcher(lowered)
            if not notes:
                continue
            assessment = ThreatAssessment(
                threat_type=rule.threat_type,
                confidence=rule.confidence,
                notes=f"{rule.name}: {notes}",
            )
            await self.publisher.publish(
                guardrail_triggered_event(
                    run_id,
                    layer="input",
                    assessment=assessment,
                )
            )
            raise GuardrailViolation("input", assessment, notes)
        if mode == ChatMode.RESEARCH and self._contains_prohibited_payload(text):
            # Research mode should never carry executable payloads
            assessment = ThreatAssessment(
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=ThreatConfidence.MEDIUM,
                notes="structured payload embedded in research request",
            )
            await self.publisher.publish(
                guardrail_triggered_event(run_id, layer="input", assessment=assessment)
            )
            raise GuardrailViolation("input", assessment, assessment.notes or "")

    def _build_rules(self) -> list[GuardrailRule]:
        rules: list[GuardrailRule] = [
            GuardrailRule(
                name="system_instruction_override",
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=ThreatConfidence.HIGH,
                matcher=self._match_instruction_override,
                category="override",
            ),
            GuardrailRule(
                name="disallowed_action_request",
                threat_type=ThreatType.POLICY_VIOLATION,
                confidence=ThreatConfidence.MEDIUM,
                matcher=self._match_disallowed_action,
                category="action",
            ),
            GuardrailRule(
                name="internal_policy_probe",
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=ThreatConfidence.MEDIUM,
                matcher=self._match_policy_probe,
                category="leak",
            ),
            GuardrailRule(
                name="unexpected_structured_payload",
                threat_type=ThreatType.UNEXPECTED_OUTPUT_SHAPE,
                confidence=ThreatConfidence.MEDIUM,
                matcher=self._match_structured_payload,
                category="payload",
            ),
        ]
        return rules

    @staticmethod
    def _match_instruction_override(text: str) -> str | None:
        patterns = [
            r"ignore\s+previous\s+instructions",
            r"disregard\s+all\s+prior\s+messages",
            r"you\s+are\s+now\s+in\s+developer\s+mode",
            r"system\s+prompt\s*[:=]",
            r"forget\s+the\s+rules",
            r"override\s+the\s+safety\s+constraints",
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return f"matched pattern '{pattern}'"
        return None

    @staticmethod
    def _match_disallowed_action(text: str) -> str | None:
        disallowed = [
            "build a bomb",
            "make a bomb",
            "manufacture explosives",
            "hack into",
            "launch a ddos",
            "write malware",
            "ransomware",
        ]
        for phrase in disallowed:
            if phrase in text:
                return f"disallowed request '{phrase}'"
        return None

    @staticmethod
    def _match_policy_probe(text: str) -> str | None:
        patterns = [
            r"what\s+is\s+your\s+system\s+prompt",
            r"show\s+internal\s+instructions",
            r"reveal\s+hidden\s+polic(?:y|ies)",
            r"tell\s+me\s+the\s+exact\s+rules",
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return f"matched pattern '{pattern}'"
        return None

    def _match_structured_payload(self, text: str) -> str | None:
        if not self._looks_like_json_payload(text) and "Role::system" not in text:
            return None
        return "structured payload detected"

    @staticmethod
    def _looks_like_json_payload(text: str) -> bool:
        snippet = text.strip()
        if not (snippet.startswith("{") and snippet.endswith("}")):
            return False
        try:
            payload = json.loads(snippet)
        except json.JSONDecodeError:
            return False
        suspicious_keys = {"role", "instructions", "system_prompt", "policies"}
        return any(key in payload for key in suspicious_keys)

    @staticmethod
    def _contains_prohibited_payload(text: str) -> bool:
        if "BEGIN PROMPT" in text.upper():
            return True
        xml_trigger = re.search(r"<\s*(prompt|instructions)\b", text, re.IGNORECASE)
        return bool(xml_trigger)
