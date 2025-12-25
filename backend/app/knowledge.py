"""Shared corpus metadata helpers."""

from __future__ import annotations

import threading

_corpus_version = "unknown"
_lock = threading.Lock()


def set_corpus_version(version: str) -> None:
    """Update the globally visible corpus version."""
    global _corpus_version
    normalized = version or "unknown"
    with _lock:
        _corpus_version = normalized


def get_corpus_version() -> str:
    """Return the current corpus version identifier."""
    with _lock:
        return _corpus_version
