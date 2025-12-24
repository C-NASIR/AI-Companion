"""Evaluation reporting utilities."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..state import RunState
from .dataset import EvalCase
from .runner import CaseRunResult, EVAL_DATA_DIR
from .scorers import ScoreResult


REPORT_PATH = EVAL_DATA_DIR / "report.json"


@dataclass
class CaseReport:
    """Per-case aggregation of scorer outputs."""

    case_id: str
    description: str
    run_id: str
    outcome: str | None
    verification_passed: bool | None
    scorer_results: list[ScoreResult]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.scorer_results)


@dataclass
class ScorerSummary:
    """Aggregated stats per scorer across all cases."""

    name: str
    passed: int
    failed: int

    def to_dict(self) -> dict[str, object]:
        total = self.passed + self.failed
        pass_rate = (self.passed / total) if total else 0.0
        return {
            "name": self.name,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(pass_rate, 3),
        }


@dataclass
class EvaluationReport:
    """Combined view of all case-level results."""

    cases: list[CaseReport]
    scorer_summaries: list[ScorerSummary]
    success: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "cases": [
                {
                    "case_id": case.case_id,
                    "run_id": case.run_id,
                    "outcome": case.outcome,
                    "verification_passed": case.verification_passed,
                    "passed": case.passed,
                    "scorers": [
                        {"name": result.name, "passed": result.passed, "details": result.details}
                        for result in case.scorer_results
                    ],
                }
                for case in self.cases
            ],
            "scorers": [summary.to_dict() for summary in self.scorer_summaries],
        }


class ReportBuilder:
    """Collects case results and produces an EvaluationReport."""

    def __init__(self):
        self._cases: list[CaseReport] = []

    def add_case(self, case: EvalCase, run_result: CaseRunResult, scores: Sequence[ScoreResult]) -> None:
        report = CaseReport(
            case_id=case.id,
            description=case.description,
            run_id=run_result.run_id,
            outcome=run_result.outcome,
            verification_passed=run_result.verification_passed,
            scorer_results=list(scores),
        )
        self._cases.append(report)

    def build(self) -> EvaluationReport:
        scorer_totals: dict[str, ScorerSummary] = {}
        for case in self._cases:
            for result in case.scorer_results:
                summary = scorer_totals.setdefault(
                    result.name,
                    ScorerSummary(name=result.name, passed=0, failed=0),
                )
                if result.passed:
                    summary.passed += 1
                else:
                    summary.failed += 1
        summaries = list(scorer_totals.values())
        success = all(case.passed for case in self._cases)
        return EvaluationReport(cases=self._cases, scorer_summaries=summaries, success=success)


def print_report(report: EvaluationReport) -> None:
    """Render a human-readable summary table to stdout."""
    title = "Evaluation Report"
    separator = "=" * len(title)
    print(separator)
    print(title)
    print(separator)
    status_text = "PASS" if report.success else "FAIL"
    print(f"Overall status: {status_text}")
    print()
    print("Cases:")
    for case in report.cases:
        mark = "✓" if case.passed else "✗"
        print(f"  {mark} {case.case_id} (run={case.run_id}) outcome={case.outcome}")
        for result in case.scorer_results:
            prefix = "    ✓" if result.passed else "    ✗"
            print(f"{prefix} {result.name}: {result.details}")
    print()
    print("Scorer summary:")
    for summary in report.scorer_summaries:
        stats = summary.to_dict()
        print(
            f"  {summary.name}: passed={summary.passed} failed={summary.failed} "
            f"pass_rate={stats['pass_rate']:.2f}"
        )
    print(separator)


def write_report(report: EvaluationReport, path: Path | None = None) -> Path:
    """Persist the machine-readable report."""
    target = Path(path) if path else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


__all__ = [
    "CaseReport",
    "ScorerSummary",
    "EvaluationReport",
    "ReportBuilder",
    "print_report",
    "write_report",
]
