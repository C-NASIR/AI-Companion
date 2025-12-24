"""API router for Session 5 event-driven backend."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .coordinator import RunCoordinator
from .events import EventBus, EventStore, sse_event_stream
from .ingestion import EmbeddingGenerator
from .mcp.client import MCPClient
from .mcp.registry import MCPRegistry
from .permissions import PermissionGate
from .retrieval import InMemoryRetrievalStore, configure_retrieval_store
from .schemas import ChatRequest, FeedbackRequest, iso_timestamp
from .state import RunState
from .state_store import StateStore
from .observability.store import TraceStore
from .observability.tracer import Tracer
from .observability.api import configure_trace_api, router as observability_router
from .workflow import ActivityContext, WorkflowEngine, WorkflowStore, build_activity_map

router = APIRouter()
logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
EVENTS_DIR = DATA_DIR / "events"
STATE_DIR = DATA_DIR / "state"
WORKFLOW_DIR = DATA_DIR / "workflow"
TRACE_DIR = DATA_DIR / "traces"

DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENT_STORE = EventStore(EVENTS_DIR)
EVENT_BUS = EventBus(EVENT_STORE)
STATE_STORE = StateStore(STATE_DIR)
EMBEDDING_GENERATOR = EmbeddingGenerator()
RETRIEVAL_STORE = InMemoryRetrievalStore(EMBEDDING_GENERATOR.embed)
configure_retrieval_store(RETRIEVAL_STORE)
MCP_REGISTRY = MCPRegistry()
PERMISSION_GATE = PermissionGate()
MCP_CLIENT = MCPClient(MCP_REGISTRY)
WORKFLOW_STORE = WorkflowStore(WORKFLOW_DIR)
TRACE_STORE = TraceStore(TRACE_DIR)
TRACER = Tracer(TRACE_STORE)
configure_trace_api(TRACE_STORE)
router.include_router(observability_router)


def _allowed_tools_provider(state: RunState):
    context = PERMISSION_GATE.build_context(
        user_role="human",
        run_type=state.mode.value,
        is_evaluation=state.is_evaluation,
    )
    return PERMISSION_GATE.filter_allowed(
        MCP_REGISTRY.list_tools(),
        context,
    )


ACTIVITY_CONTEXT = ActivityContext(
    EVENT_BUS,
    STATE_STORE,
    RETRIEVAL_STORE,
    allowed_tools_provider=_allowed_tools_provider,
    tracer=TRACER,
)
ACTIVITY_MAP = build_activity_map(ACTIVITY_CONTEXT)
WORKFLOW_ENGINE = WorkflowEngine(
    EVENT_BUS,
    WORKFLOW_STORE,
    STATE_STORE,
    activities=ACTIVITY_MAP,
    activity_context=ACTIVITY_CONTEXT,
    tracer=TRACER,
)
RUN_COORDINATOR = RunCoordinator(
    EVENT_BUS,
    STATE_STORE,
    WORKFLOW_ENGINE,
    ACTIVITY_CONTEXT,
    TRACER,
)


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]


def _log(message: str, run_id: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})


@router.post("/runs")
async def create_run(
    request: Request,
    payload: ChatRequest,
    x_run_id: str | None = Header(default=None, alias="X_Run_Id"),
) -> JSONResponse:
    """Start a new run and return immediately."""
    run_id = x_run_id or str(uuid.uuid4())
    context_length = len(payload.context or "")
    _log(
        "run request mode=%s message_length=%s context_length=%s client=%s",
        run_id,
        payload.mode.value,
        len(payload.message),
        context_length,
        request.client.host if request.client else "unknown",
    )

    state = RunState.new(
        run_id=run_id,
        message=payload.message,
        context=payload.context,
        mode=payload.mode,
    )

    await RUN_COORDINATOR.start_run(state)
    return JSONResponse({"ok": True, "run_id": run_id})


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Replay stored events and stream new ones using SSE."""

    async def event_generator():
        async for chunk in sse_event_stream(run_id, EVENT_STORE, EVENT_BUS):
            yield chunk

    response = StreamingResponse(event_generator(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["X-Run-Id"] = run_id
    return response


@router.get("/runs/{run_id}/state")
async def run_state(run_id: str) -> JSONResponse:
    """Return the latest stored RunState snapshot."""
    state = STATE_STORE.load(run_id)
    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return JSONResponse(state.model_dump())


@router.get("/runs/{run_id}/workflow")
async def run_workflow_state(run_id: str) -> JSONResponse:
    """Return the persisted workflow state for the run."""
    workflow_state = WORKFLOW_STORE.load(run_id)
    if not workflow_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    return JSONResponse(workflow_state.model_dump())


@router.post("/runs/{run_id}/approval")
async def run_approval_endpoint(run_id: str, payload: ApprovalRequest) -> JSONResponse:
    """Record a human approval decision and resume the workflow."""
    workflow_state = WORKFLOW_STORE.load(run_id)
    if not workflow_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    await WORKFLOW_ENGINE.record_human_decision(run_id, payload.decision)
    return JSONResponse({"status": "recorded"})


@router.post("/feedback")
async def feedback_endpoint(payload: FeedbackRequest) -> JSONResponse:
    """Persist structured feedback tied to a prior run."""
    run_id = payload.run_id
    _log(
        "feedback received score=%s mode=%s",
        run_id,
        payload.score.value,
        payload.mode.value,
    )
    record = payload.model_dump()
    record["ts"] = iso_timestamp()

    try:
        with FEEDBACK_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:
        logger.exception("failed to persist feedback", extra={"run_id": run_id})
        return JSONResponse(
            {"status": "error", "message": "Unable to record feedback."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _log("feedback stored", run_id)
    return JSONResponse({"status": "recorded"}, status_code=status.HTTP_201_CREATED)
