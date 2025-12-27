"""Environment loading helpers.

These helpers are intentionally not invoked at import time. Call them explicitly
from application entrypoints to control side effects.
"""

from __future__ import annotations


def load_dotenv_if_present() -> None:
    """Load environment variables from a .env file if available."""

    try:
        from dotenv import find_dotenv, load_dotenv
    except Exception:
        return

    dotenv_path = find_dotenv(filename=".env", usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

