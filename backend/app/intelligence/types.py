"""Shared types for the intelligence graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..state import RunPhase, RunState

NodeFunc = Callable[[RunState, "NodeContext"], Awaitable[RunState]]


@dataclass(frozen=True)
class NodeSpec:
    """Definition of a control graph node."""

    name: str
    phase: RunPhase
    func: NodeFunc
