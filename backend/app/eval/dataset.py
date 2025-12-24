"""Evaluation dataset loader and schema validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..schemas import ChatMode


DATASET_PATH = Path(__file__).with_name("dataset.yaml")


class CaseInput(BaseModel):
    """User-facing inputs captured for a dataset entry."""

    message: str = Field(..., min_length=1)
    context: str | None = None


class CaseExpectations(BaseModel):
    """Structured expectations enforced by evaluation scorers."""

    outcome: str = Field(..., pattern="^(success|refusal|failure)$")
    requires_retrieval: bool
    requires_tool: str | None = None
    forbidden_tool: str | None = None
    requires_citations: bool
    max_tool_calls: int | None = Field(default=None, ge=0)
    verification_should_fail: bool
    notes: str = Field(..., min_length=3)

    @field_validator("requires_tool", "forbidden_tool")
    @classmethod
    def _normalize_tool_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_tool_constraints(self) -> "CaseExpectations":
        if self.requires_tool and self.forbidden_tool:
            msg = "requires_tool and forbidden_tool are mutually exclusive"
            raise ValueError(msg)
        return self


class EvalCase(BaseModel):
    """Single evaluation case definition."""

    id: str = Field(..., min_length=3)
    description: str = Field(..., min_length=5)
    input: CaseInput
    mode: ChatMode
    expectations: CaseExpectations


class EvaluationDataset(BaseModel):
    """Full dataset wrapper used for validation and lookups."""

    cases: list[EvalCase]

    @model_validator(mode="after")
    def _check_cases(self) -> "EvaluationDataset":
        count = len(self.cases)
        if count < 30 or count > 60:
            msg = f"dataset must contain between 30 and 60 cases (found {count})"
            raise ValueError(msg)
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                msg = f"duplicate case id detected: {case.id}"
                raise ValueError(msg)
            seen.add(case.id)
        return self

    def by_id(self, case_id: str) -> EvalCase | None:
        """Return the case matching the provided id, if present."""
        for case in self.cases:
            if case.id == case_id:
                return case
        return None

    def __iter__(self) -> Iterable[EvalCase]:
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)


def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    if payload is None:
        return {"cases": []}
    if isinstance(payload, list):
        return {"cases": payload}
    if isinstance(payload, dict):
        return payload
    msg = f"unsupported dataset format type={type(payload)}"
    raise TypeError(msg)


def load_dataset(path: str | Path | None = None) -> EvaluationDataset:
    """Load and validate the evaluation dataset."""
    dataset_path = Path(path) if path else DATASET_PATH
    payload = _load_yaml(dataset_path)
    try:
        return EvaluationDataset.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid evaluation dataset: {exc}") from exc


def list_case_ids(dataset: EvaluationDataset | None = None) -> Sequence[str]:
    """Helper to list case ids for quick sanity checks."""
    data = dataset or load_dataset()
    return [case.id for case in data]


__all__ = [
    "CaseExpectations",
    "CaseInput",
    "EvalCase",
    "EvaluationDataset",
    "DATASET_PATH",
    "load_dataset",
    "list_case_ids",
]
