"""API router coordinating streaming intelligence graph."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import AsyncGenerator

import json
from pathlib import Path

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from .intelligence import run_graph
from .schemas import (
    ChatRequest,
    FeedbackRequest,
    build_event,
    iso_timestamp,
    serialize_event,
)
from .state import RunState

router = APIRouter()
logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _log(message: str, run_id: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})


@router.post("/chat")
async def chat_endpoint(
    request: Request,
    payload: ChatRequest,
    x_run_id: str | None = Header(default=None, alias="X_Run_Id"),
) -> StreamingResponse:
    """Execute the intelligence graph and stream NDJSON events."""
    run_id = x_run_id or str(uuid.uuid4())
    context_length = len(payload.context or "")
    _log(
        "request received mode=%s message_length=%s context_length=%s client=%s",
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

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def emit(event_type: str, data: dict[str, object]) -> None:
            await queue.put(serialize_event(build_event(event_type, run_id, data)))

        async def graph_runner() -> None:
            _log("graph execution started", run_id)
            try:
                await run_graph(state, emit)
            except Exception:
                logger.exception("graph execution failed", extra={"run_id": run_id})
                await emit(
                    "error",
                    {"message": "Unexpected error while generating a response."},
                )
                await emit(
                    "done",
                    {
                        "final_text": state.output_text,
                        "outcome": "failed",
                        "reason": "internal error",
                    },
                )
            finally:
                _log("graph execution ended", run_id)
                await queue.put(None)

        task = asyncio.create_task(graph_runner())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            _log("request completed", run_id)

    try:
        response = StreamingResponse(
            event_stream(), media_type="application/x-ndjson"
        )
        response.headers["X_Run_Id"] = run_id
        return response
    except Exception:
        logger.exception("request failed", extra={"run_id": run_id})
        raise


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
