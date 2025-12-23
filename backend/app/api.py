"""API router for Session 5 event-driven backend."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from .coordinator import RunCoordinator
from .events import EventBus, EventStore, sse_event_stream
from .ingestion import EmbeddingGenerator
from .retrieval import InMemoryRetrievalStore, configure_retrieval_store
from .schemas import ChatRequest, FeedbackRequest, iso_timestamp
from .state import RunState
from .state_store import StateStore

router = APIRouter()
logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
EVENTS_DIR = DATA_DIR / "events"
STATE_DIR = DATA_DIR / "state"

DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENT_STORE = EventStore(EVENTS_DIR)
EVENT_BUS = EventBus(EVENT_STORE)
STATE_STORE = StateStore(STATE_DIR)
EMBEDDING_GENERATOR = EmbeddingGenerator()
RETRIEVAL_STORE = InMemoryRetrievalStore(EMBEDDING_GENERATOR.embed)
configure_retrieval_store(RETRIEVAL_STORE)
RUN_COORDINATOR = RunCoordinator(EVENT_BUS, STATE_STORE, RETRIEVAL_STORE)


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
