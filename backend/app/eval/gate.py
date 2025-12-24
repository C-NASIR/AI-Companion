"""Gatekeeper enforcing evaluation pass criteria."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .report import EvaluationReport


@dataclass
class GateConfig:
    """Configuration for evaluation gating."""

    allow_failures: int = 0


class Gatekeeper:
    """Determines whether the evaluation report meets pass criteria."""

    def __init__(self, config: GateConfig | None = None):
        self.config = config or GateConfig()

    def evaluate(self, report: EvaluationReport) -> tuple[bool, list[str]]:
        """Return (passed, details) and failing reasons."""
        if report.success:
            return True, []
        allowed = self.config.allow_failures
        failures = self._collect_failures(report)
        if len(failures) <= allowed:
            return True, failures
        return False, failures

    def enforce(self, report: EvaluationReport) -> None:
        """Raise RuntimeError when the gate fails."""
        passed, failures = self.evaluate(report)
        if passed:
            return
        formatted = "\n".join(failures) if failures else "Unknown evaluation failure."
        raise RuntimeError(f"Evaluation gate failed:\n{formatted}")

    def _collect_failures(self, report: EvaluationReport) -> list[str]:
        reasons: list[str] = []
        for case in report.cases:
            if case.passed:
                continue
            failed_scorers = [result for result in case.scorer_results if not result.passed]
            scorer_details = ", ".join(f"{result.name}: {result.details}" for result in failed_scorers)
            reasons.append(f"- case {case.case_id} (run={case.run_id}) failed -> {scorer_details}")
        return reasons


__all__ = ["Gatekeeper", "GateConfig"]
