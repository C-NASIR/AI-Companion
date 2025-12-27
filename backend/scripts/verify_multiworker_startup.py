"""Quick multi-worker startup sanity check.

This is an operational check (not a unit test). It starts uvicorn with multiple
workers briefly to ensure imports + startup hooks are compatible with
multi-process execution.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start uvicorn with multiple workers briefly and exit."
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--workers",
        str(max(args.workers, 1)),
        "--port",
        str(args.port),
        "--log-level",
        "info",
    ]
    print("running:", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    try:
        time.sleep(max(args.seconds, 0.1))
    finally:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

