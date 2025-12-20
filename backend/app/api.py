"""API router for Session 0 backend."""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .model import stream_chat

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


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
    _log(
        "request received message_length=%s client=%s",
        run_id,
        len(payload.message),
        request.client.host if request.client else "unknown",
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        _log("model call started", run_id)
        try:
            async for chunk in stream_chat(payload.message, run_id):
                yield chunk
        except Exception:
            logger.exception("model stream error", extra={"run_id": run_id})
            raise
        finally:
            _log("model stream ended", run_id)
            _log("request completed", run_id)

    try:
        response = StreamingResponse(event_stream(), media_type="text/plain")
        response.headers["X_Run_Id"] = run_id
        return response
    except Exception:
        logger.exception("request failed", extra={"run_id": run_id})
        raise
