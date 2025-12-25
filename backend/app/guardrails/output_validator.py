"""Final output validation before responses reach the user."""

from __future__ import annotations

import re
from ..events import guardrail_triggered_event
from ..schemas import ChatMode
from ..state import PlanType, RunState
from .base import EventPublisher, GuardrailViolation
from .threats import ThreatAssessment, ThreatConfidence, ThreatType


CITATION_PATTERN = re.compile(r"\[([^\]]+)\]")
TOOL_REFERENCE_PATTERN = re.compile(r"\{\{tool:([a-zA-Z0-9_\-]+)\}\}")
UNEXPECTED_FORMAT_PATTERN = re.compile(r"^\s*[{<\[]", re.MULTILINE)
POLICY_BANNED_TERMS = [
    "explosive recipe",
    "weapon design",
    "zero-day exploit",
    "manufacture drugs",
    "credit card dump",
]


class OutputValidator:
    """Guards against malformed or unsafe output content."""

    def __init__(self, publisher: EventPublisher):
        self.publisher = publisher

    async def validate(
        self,
        state: RunState,
        *,
        enforce_citations: bool = True,
    ) -> None:
        text = (state.output_text or "").strip()
        if not text:
            await self._trigger(
                state,
                ThreatAssessment(
                    threat_type=ThreatType.UNEXPECTED_OUTPUT_SHAPE,
                    confidence=ThreatConfidence.MEDIUM,
                    notes="output text is empty",
                ),
            )

        require_citations = bool(state.retrieved_chunks) and (
            state.plan_type == PlanType.DIRECT_ANSWER or state.mode == ChatMode.RESEARCH
        )
        if enforce_citations and require_citations and not self._contains_required_citations(
            text, state.retrieved_chunks
        ):
            await self._trigger(
                state,
                ThreatAssessment(
                    threat_type=ThreatType.UNEXPECTED_OUTPUT_SHAPE,
                    confidence=ThreatConfidence.HIGH,
                    notes="missing citations despite retrieval",
                ),
            )

        missing_tools = self._detect_unavailable_tool_references(text, state)
        if missing_tools:
            await self._trigger(
                state,
                ThreatAssessment(
                    threat_type=ThreatType.TOOL_ABUSE,
                    confidence=ThreatConfidence.MEDIUM,
                    notes=f"referenced unavailable tools: {', '.join(sorted(missing_tools))}",
                ),
            )

        banned_term = self._detect_policy_violation(text)
        if banned_term:
            await self._trigger(
                state,
                ThreatAssessment(
                    threat_type=ThreatType.POLICY_VIOLATION,
                    confidence=ThreatConfidence.MEDIUM,
                    notes=f"detected banned content '{banned_term}'",
                ),
            )

        if self._unexpected_format(text):
            await self._trigger(
                state,
                ThreatAssessment(
                    threat_type=ThreatType.UNEXPECTED_OUTPUT_SHAPE,
                    confidence=ThreatConfidence.MEDIUM,
                    notes="output looks like structured payload",
                ),
            )

    async def _trigger(self, state: RunState, assessment: ThreatAssessment) -> None:
        await self.publisher.publish(
            guardrail_triggered_event(
                state.run_id,
                layer="output",
                assessment=assessment,
            )
        )
        raise GuardrailViolation("output", assessment, assessment.notes or "")

    @staticmethod
    def _contains_required_citations(text: str, chunks) -> bool:
        if not text:
            return False
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        for match in CITATION_PATTERN.finditer(text):
            if match.group(1) in chunk_ids:
                return True
        return False

    @staticmethod
    def _detect_unavailable_tool_references(text: str, state: RunState) -> set[str]:
        allowed = {entry.name for entry in state.available_tools}
        referenced = {match.group(1) for match in TOOL_REFERENCE_PATTERN.finditer(text)}
        return {name for name in referenced if name not in allowed}

    @staticmethod
    def _detect_policy_violation(text: str) -> str | None:
        lowered = text.lower()
        for term in POLICY_BANNED_TERMS:
            if term in lowered:
                return term
        return None

    @staticmethod
    def _unexpected_format(text: str) -> bool:
        if not text:
            return True
        if UNEXPECTED_FORMAT_PATTERN.match(text):
            return True
        if text.count("{") >= 8 or text.count("[") >= 8:
            return True
        return False
