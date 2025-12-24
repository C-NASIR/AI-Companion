"""Module entrypoint so `python -m app.eval.run` executes the evaluation CLI."""

from __future__ import annotations

import asyncio
import sys

from .cli import main as cli_main


def main() -> int:
    """Invoke the async CLI and return the resulting exit code."""
    return asyncio.run(cli_main(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
