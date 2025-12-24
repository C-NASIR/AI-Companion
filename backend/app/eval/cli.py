"""Command-line interface for running the evaluation suite."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Sequence

from ..api import EVENT_STORE, STATE_STORE, TRACE_STORE
from .dataset import EvaluationDataset, load_dataset
from .gate import GateConfig, Gatekeeper
from .report import ReportBuilder, print_report, write_report
from .runner import EvaluationRunner
from .scorers import run_scorers
from .trajectory import TrajectoryExtractor


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AI Companion evaluation suite.")
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Run only the specified case id (can be provided multiple times).",
    )
    parser.add_argument(
        "--allow-failures",
        type=int,
        default=0,
        help="Maximum number of allowed case failures before the gate fails (default: 0).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override per-case timeout in seconds.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


async def _run_evaluation(
    dataset: EvaluationDataset,
    *,
    case_ids: Sequence[str] | None,
    timeout_seconds: float | None,
    allow_failures: int,
) -> int:
    runner = EvaluationRunner(dataset=dataset, timeout_seconds=timeout_seconds or 120)
    trajectory_extractor = TrajectoryExtractor(STATE_STORE, EVENT_STORE, TRACE_STORE)
    builder = ReportBuilder()

    run_results = await runner.run_all(case_ids)
    id_to_case = {case.id: case for case in dataset}
    for result in run_results:
        case = id_to_case.get(result.case_id)
        if not case:
            raise RuntimeError(f"case {result.case_id} not found in dataset")
        trajectory = trajectory_extractor.extract(result.run_id, case_id=case.id)
        scores = run_scorers(case, trajectory)
        builder.add_case(case, result, scores)

    report = builder.build()
    print_report(report)
    write_report(report)
    gate = Gatekeeper(GateConfig(allow_failures=max(0, allow_failures)))
    gate.enforce(report)
    return 0


async def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        dataset = load_dataset()
        return await _run_evaluation(
            dataset,
            case_ids=args.cases,
            timeout_seconds=args.timeout,
            allow_failures=args.allow_failures,
        )
    except KeyboardInterrupt:
        print("Evaluation interrupted.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1


def entrypoint() -> None:
    """Synchronously run the async CLI for convenience."""
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
