"""Durable persistence for workflow state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .models import WorkflowState


class WorkflowStore:
    """Persist WorkflowState objects as JSON files."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.json"

    def save(self, state: WorkflowState) -> WorkflowState:
        """Persist the provided workflow state snapshot."""
        path = self._path(state.run_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state.model_dump(), handle, ensure_ascii=False, indent=2)
        return state

    def load(self, run_id: str) -> WorkflowState | None:
        """Load a persisted workflow state if available."""
        path = self._path(run_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return WorkflowState.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None

    def load_or_create(self, run_id: str) -> WorkflowState:
        """Load an existing workflow state or initialize a new one."""
        existing = self.load(run_id)
        if existing:
            return existing
        state = WorkflowState(run_id=run_id)
        return self.save(state)

    def update(self, run_id: str, mutator: Callable[[WorkflowState], None]) -> WorkflowState:
        """Apply a mutation and persist atomically."""
        state = self.load_or_create(run_id)
        mutator(state)
        return self.save(state)
