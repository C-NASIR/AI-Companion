"""Graph wiring for the intelligence workflow."""

from __future__ import annotations

from .nodes.finalize import finalize_node
from .nodes.planner import plan_node
from .nodes.receive import receive_node
from .nodes.response import respond_node
from .nodes.retrieval import retrieve_node
from .types import NodeSpec
from .verification import verify_node
from ..state import RunPhase

GRAPH: list[NodeSpec] = [
    NodeSpec("receive", RunPhase.RECEIVE, receive_node),
    NodeSpec("plan", RunPhase.PLAN, plan_node),
    NodeSpec("retrieve", RunPhase.RETRIEVE, retrieve_node),
    NodeSpec("respond", RunPhase.RESPOND, respond_node),
    NodeSpec("verify", RunPhase.VERIFY, verify_node),
    NodeSpec("finalize", RunPhase.FINALIZE, finalize_node),
]

NODE_SEQUENCE = [spec.name for spec in GRAPH]
NODE_MAP = {spec.name: spec for spec in GRAPH}
NEXT_NODE: dict[str, str] = {
    current: NODE_SEQUENCE[idx + 1]
    for idx, current in enumerate(NODE_SEQUENCE[:-1])
}
