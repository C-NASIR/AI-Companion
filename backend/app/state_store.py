"""Durable RunState persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .state import RunState


class StateStore:
    """Persist RunState snapshots as JSON files."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.json"

    def save(self, state: RunState) -> None:
        """Serialize the provided state snapshot to disk."""
        path = self._path(state.run_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state.model_dump(), handle, ensure_ascii=False, indent=2)

    def load(self, run_id: str) -> Optional[RunState]:
        """Load the stored RunState or return None if missing/invalid."""
        path = self._path(run_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return RunState.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None
