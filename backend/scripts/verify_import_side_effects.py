"""Verify that importing backend modules does not write to data directories.

This is an operational check (not a unit test). It helps catch regressions where
module imports trigger runtime wiring or persistence side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _snapshot(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    if path.is_file():
        stat = path.stat()
        return {"exists": True, "type": "file", "mtime": stat.st_mtime, "size": stat.st_size}
    files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())
    stat = path.stat()
    return {
        "exists": True,
        "type": "dir",
        "mtime": stat.st_mtime,
        "file_count": len(files),
        "files": files,
    }


def _run_imports(modules: list[str]) -> int:
    code = "\n".join([f"import {m}" for m in modules]) + "\nprint('ok')\n"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify import-only runs do not modify data directories."
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=[
            "backend/data",
            "backend/data/events",
            "backend/data/state",
            "backend/data/workflow",
            "backend/data/traces",
            "backend/data/feedback.jsonl",
        ],
        help="Filesystem paths to snapshot before/after imports.",
    )
    parser.add_argument(
        "--modules",
        nargs="+",
        default=["backend.app.api", "backend.app.main"],
        help="Python modules to import.",
    )
    args = parser.parse_args()

    snapshot_paths = [Path(p) for p in args.paths]
    before = {str(p): _snapshot(p) for p in snapshot_paths}

    rc = _run_imports(list(args.modules))
    if rc != 0:
        return rc

    after = {str(p): _snapshot(p) for p in snapshot_paths}
    changed = {p: {"before": before[p], "after": after[p]} for p in before if before[p] != after[p]}

    if changed:
        print("import_side_effects_detected=true")
        print(json.dumps(changed, indent=2, sort_keys=True))
        return 1

    print("import_side_effects_detected=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

