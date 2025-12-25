"""Continuous evaluation loop to prove the system can run for hours without attention."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import Sequence

from app.api import STATE_STORE
from app.eval.runner import CaseRunResult, EvaluationRunner


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run evaluation cases on a loop to verify boring operation."
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=None,
        help="Run for approximately this many minutes (default: disabled).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of times to replay the full dataset (default: 1 unless duration specified).",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=None,
        help="Optional subset of evaluation case ids to run.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-run timeout in seconds (default: 120).",
    )
    return parser.parse_args()


def _summarize(total: int, failures: int, degraded_runs: int, durations: Sequence[float]) -> None:
    avg_duration = statistics.mean(durations) if durations else 0.0
    if len(durations) >= 100:
        p95_duration = statistics.quantiles(durations, n=100)[94]
    else:
        p95_duration = max(durations, default=0.0)
    print("\n=== Boring operation summary ===")
    print(f"Total runs: {total}")
    print(f"Failures: {failures}")
    print(f"Degraded runs: {degraded_runs}")
    print(f"Average duration: {avg_duration:.2f}s")
    print(f"P95 duration: {p95_duration:.2f}s")


async def _run_soak(args: argparse.Namespace) -> int:
    iterations = args.iterations
    duration_seconds = args.duration_minutes * 60 if args.duration_minutes else None
    if iterations is None and duration_seconds is None:
        iterations = 1
    runner = EvaluationRunner(timeout_seconds=args.timeout_seconds)
    total_runs = 0
    failures = 0
    durations: list[float] = []
    degraded_runs = 0
    start = time.perf_counter()
    try:
        async for result in runner.soak(
            iterations=iterations,
            duration_seconds=duration_seconds,
            case_ids=args.cases,
        ):
            total_runs += 1
            durations.append(result.duration_seconds)
            if result.event_type != "run.completed" or result.outcome != "success":
                failures += 1
            state = STATE_STORE.load(result.run_id)
            if state and state.degraded:
                degraded_runs += 1
            if duration_seconds is not None and (time.perf_counter() - start) >= duration_seconds:
                break
    except KeyboardInterrupt:
        print("\nInterrupted, summarizing partial results…")
    _summarize(total_runs, failures, degraded_runs, durations)
    return 0 if total_runs else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run_soak(args))


if __name__ == "__main__":
    sys.exit(main())
