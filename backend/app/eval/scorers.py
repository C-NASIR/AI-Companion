"""Evaluation scorers that validate trajectories against dataset expectations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Protocol

from .dataset import EvalCase
from .trajectory import Trajectory


@dataclass
class ScoreResult:
    """Structured result describing a single scorer outcome."""

    name: str
    passed: bool
    details: str


class TrajectoryScorer(Protocol):
    """Interface implemented by all scorers."""

    name: str

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        ...


def _bool_to_word(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


class OutcomeScorer:
    """Checks that run outcome/refusal/failure matches expectation."""

    name = "outcome"

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        expected = case.expectations.outcome
        actual = trajectory.state.outcome or "unknown"
        passed = expected == actual
        if not passed:
            details = f"expected outcome={expected}, got={actual}"
        else:
            details = f"outcome={actual}"
        return ScoreResult(name=self.name, passed=passed, details=details)


class RetrievalScorer:
    """Validates retrieval behavior against requires_retrieval flag."""

    name = "retrieval"

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        required = case.expectations.requires_retrieval
        retrievals = trajectory.retrievals
        retrieved_chunks = trajectory.state.retrieved_chunks or []
        performed = bool(retrievals and retrievals[-1].chunk_ids)
        if required and not performed:
            details = "retrieval required but no chunks were stored"
            return ScoreResult(name=self.name, passed=False, details=details)
        details = f"retrieval_performed={performed} chunks={len(retrieved_chunks)}"
        return ScoreResult(name=self.name, passed=True, details=details)


class ToolUsageScorer:
    """Ensures required or forbidden tools and max call counts are respected."""

    name = "tool_usage"

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        expected_tool = case.expectations.requires_tool
        forbidden_tool = case.expectations.forbidden_tool
        max_calls = case.expectations.max_tool_calls
        tool_calls = trajectory.tool_calls
        call_count = len([call for call in tool_calls if call.status in {"completed", "failed", "denied"}])
        names = [call.name for call in tool_calls]
        if expected_tool and expected_tool not in names:
            return ScoreResult(
                name=self.name,
                passed=False,
                details=f"expected tool {expected_tool} not invoked",
            )
        if forbidden_tool and forbidden_tool in names:
            return ScoreResult(
                name=self.name,
                passed=False,
                details=f"forbidden tool {forbidden_tool} was invoked",
            )
        if max_calls is not None and call_count > max_calls:
            return ScoreResult(
                name=self.name,
                passed=False,
                details=f"tool call count {call_count} exceeded limit {max_calls}",
            )
        details = f"tool_calls={call_count} names={names or ['none']}"
        return ScoreResult(name=self.name, passed=True, details=details)


class GroundingScorer:
    """Checks citation requirements against retrieved chunks."""

    name = "grounding"

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        require_citations = case.expectations.requires_citations
        retrieved_chunks = trajectory.state.retrieved_chunks or []
        final_text = trajectory.state.output_text or ""
        cited_ids = self._extract_citations(final_text)
        valid_ids = {chunk.chunk_id for chunk in retrieved_chunks}

        if not require_citations:
            details = f"citations_present={bool(cited_ids)}"
            return ScoreResult(name=self.name, passed=True, details=details)

        if retrieved_chunks and not cited_ids:
            return ScoreResult(
                name=self.name,
                passed=False,
                details="citations required but none found",
            )
        missing = [citation for citation in cited_ids if citation not in valid_ids]
        if missing:
            return ScoreResult(
                name=self.name,
                passed=False,
                details=f"invalid citations detected: {missing}",
            )
        details = f"citations_ok count={len(cited_ids)}"
        return ScoreResult(name=self.name, passed=True, details=details)

    @staticmethod
    def _extract_citations(text: str) -> list[str]:
        citations: list[str] = []
        current = []
        inside = False
        for char in text:
            if char == "[":
                inside = True
                current = []
            elif char == "]" and inside:
                inside = False
                citation = "".join(current).strip()
                if citation:
                    citations.append(citation)
            elif inside:
                current.append(char)
        return citations


class VerificationScorer:
    """Compares verification results with expectations (pass/fail)."""

    name = "verification"

    def score(self, case: EvalCase, trajectory: Trajectory) -> ScoreResult:
        should_fail = case.expectations.verification_should_fail
        verification_passed = trajectory.state.verification_passed
        if should_fail and verification_passed:
            return ScoreResult(
                name=self.name,
                passed=False,
                details="verification should fail but passed",
            )
        if not should_fail and verification_passed is False:
            reason = trajectory.state.verification_reason or "unknown"
            return ScoreResult(
                name=self.name,
                passed=False,
                details=f"verification unexpectedly failed reason={reason}",
            )
        details = f"verification_passed={_bool_to_word(verification_passed)}"
        return ScoreResult(name=self.name, passed=True, details=details)


def run_scorers(case: EvalCase, trajectory: Trajectory) -> list[ScoreResult]:
    """Run all built-in scorers for a provided case + trajectory."""
    scorers: Iterable[TrajectoryScorer] = [
        OutcomeScorer(),
        RetrievalScorer(),
        ToolUsageScorer(),
        GroundingScorer(),
        VerificationScorer(),
    ]
    results: List[ScoreResult] = []
    for scorer in scorers:
        try:
            results.append(scorer.score(case, trajectory))
        except Exception as exc:  # pragma: no cover - defensive reporting
            results.append(
                ScoreResult(
                    name=scorer.name,
                    passed=False,
                    details=f"scorer error: {exc}",
                )
            )
    return results


__all__ = [
    "ScoreResult",
    "TrajectoryScorer",
    "OutcomeScorer",
    "RetrievalScorer",
    "ToolUsageScorer",
    "GroundingScorer",
    "VerificationScorer",
    "run_scorers",
]
