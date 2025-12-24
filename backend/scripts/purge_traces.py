"""Utility script to remove old trace files from backend/data/traces."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete trace files older than the specified number of days."
    )
    parser.add_argument(
        "--traces-dir",
        default=Path(__file__).resolve().parent.parent / "data" / "traces",
        type=Path,
        help="Directory containing trace json files (default: backend/data/traces)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Delete trace files last modified more than this many days ago",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching files without deleting them",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    traces_dir: Path = args.traces_dir
    if not traces_dir.exists():
        print(f"Trace directory {traces_dir} does not exist.")
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(args.days, 0))
    removed = 0
    for path in traces_dir.glob("*.json"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime > cutoff:
            continue
        if args.dry_run:
            print(f"[dry-run] would delete {path}")
            removed += 1
            continue
        path.unlink(missing_ok=True)
        removed += 1
        print(f"Deleted {path}")
    print(
        f"{'Would remove' if args.dry_run else 'Removed'} {removed} "
        f"trace file{'s' if removed != 1 else ''} older than {args.days} day(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
