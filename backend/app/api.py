"""API router for Session 0 backend."""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

import json
from pathlib import Path

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from .model import stream_chat
from .schemas import (
    ChatRequest,
    FeedbackRequest,
    build_event,
    iso_timestamp,
    serialize_event,
)

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
    """Stream chat completions via model adapter."""
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

    async def event_stream() -> AsyncGenerator[str, None]:
        # The event order mirrors the Session 1 contract: status/step scaffolding
        # precedes any model output so the UI can show progress even before
        # chunks arrive, and a done event is always emitted at the end.
        _log("model call started mode=%s", run_id, payload.mode.value)
        final_chunks: list[str] = []
        stream_events_emitted = False
        responding_emitted = False

        def emit(event_type: str, data: dict[str, object]) -> str:
            return serialize_event(build_event(event_type, run_id, data))

        def ensure_stream_events() -> list[str]:
            nonlocal stream_events_emitted
            if stream_events_emitted:
                return []
            stream_events_emitted = True
            return [
                emit("step", {"label": "Model call started", "state": "completed"}),
                emit("step", {"label": "Model streaming response", "state": "started"}),
            ]

        try:
            yield emit("status", {"value": "received"})
            yield emit("step", {"label": "Request received", "state": "started"})
            yield emit("step", {"label": "Request received", "state": "completed"})
            yield emit("status", {"value": "thinking"})
            yield emit("step", {"label": "Model call started", "state": "started"})

            async for chunk in stream_chat(
                payload.message, payload.context, payload.mode, run_id
            ):
                if not responding_emitted:
                    yield emit("status", {"value": "responding"})
                    responding_emitted = True
                    for line in ensure_stream_events():
                        yield line
                final_chunks.append(chunk)
                yield emit("output", {"text": chunk})

            for line in ensure_stream_events():
                yield line

            yield emit(
                "step", {"label": "Model streaming response", "state": "completed"}
            )
            yield emit("status", {"value": "complete"})
            yield emit("step", {"label": "Response complete", "state": "completed"})
            yield emit("done", {"final_text": "".join(final_chunks)})

        except Exception:
            logger.exception("model stream error", extra={"run_id": run_id})
            for line in ensure_stream_events():
                yield line
            yield emit(
                "step",
                {"label": "Model streaming response", "state": "completed"},
            )
            yield emit(
                "error",
                {"message": "Unexpected error while generating a response."},
            )
            yield emit("status", {"value": "complete"})
            yield emit("step", {"label": "Response complete", "state": "completed"})
            yield emit("done", {"final_text": "".join(final_chunks)})
        finally:
            _log("model stream ended", run_id)
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
