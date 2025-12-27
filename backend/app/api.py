"""API router for Session 5 event-driven backend.

This module is intentionally safe to import: it should not construct runtime
singletons or perform filesystem/network side effects.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .events import rate_limit_exceeded_event, sse_event_stream
from .schemas import ChatRequest, FeedbackRequest, iso_timestamp
from .settings import get_settings
from .state import RunState

if TYPE_CHECKING:
    from .container import BackendContainer

logger = logging.getLogger(__name__)


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]


def _log(message: str, run_id: str, *args: object) -> None:
    logger.info(message, *args, extra={"run_id": run_id})


def get_router(container: "BackendContainer") -> APIRouter:
    """Build API routes using the provided dependency container."""

    from .observability.api import router as observability_router

    router = APIRouter()
    router.include_router(observability_router)

    @router.post("/runs")
    async def create_run(
        request: Request,
        payload: ChatRequest,
        x_run_id: str | None = Header(default=None, alias="X_Run_Id"),
    ) -> JSONResponse:
        """Start a new run and return immediately."""
        run_id = x_run_id or str(uuid.uuid4())
        context_length = len(payload.context or "")
        tenant_id = payload.identity.tenant_id if payload.identity else "default"
        user_id = payload.identity.user_id if payload.identity else "anonymous"
        identity = {"tenant_id": tenant_id, "user_id": user_id}
        if not container.rate_limiter.try_acquire(run_id, tenant_id):
            await container.event_bus.publish(
                rate_limit_exceeded_event(
                    run_id,
                    scope="run_start",
                    reason="concurrency_limit",
                    metadata={"tenant_id": tenant_id},
                    identity=identity,
                )
            )
            return JSONResponse(
                {"ok": False, "reason": "rate_limited"},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        _log(
            "run request mode=%s message_length=%s context_length=%s client=%s tenant=%s user=%s",
            run_id,
            payload.mode.value,
            len(payload.message),
            context_length,
            request.client.host if request.client else "unknown",
            tenant_id,
            user_id,
        )

        state = RunState.new(
            run_id=run_id,
            message=payload.message,
            context=payload.context,
            mode=payload.mode,
            tenant_id=tenant_id,
            user_id=user_id,
            cost_limit_usd=container.settings.limits.model_budget_usd or None,
        )

        try:
            await container.run_coordinator.start_run(state)
        except Exception:
            container.rate_limiter.release(run_id)
            container.budget_manager.reset(run_id)
            raise
        return JSONResponse({"ok": True, "run_id": run_id})

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        """Replay stored events and stream new ones using SSE."""

        async def event_generator():
            async for chunk in sse_event_stream(run_id, container.event_store, container.event_bus):
                yield chunk

        response = StreamingResponse(event_generator(), media_type="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["X-Run-Id"] = run_id
        return response

    @router.get("/runs/{run_id}/state")
    async def run_state(run_id: str) -> JSONResponse:
        """Return the latest stored RunState snapshot."""
        state = container.state_store.load(run_id)
        if not state:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return JSONResponse(state.model_dump())

    @router.get("/runs/{run_id}/workflow")
    async def run_workflow_state(run_id: str) -> JSONResponse:
        """Return the persisted workflow state for the run."""
        workflow_state = container.workflow_store.load(run_id)
        if not workflow_state:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
        return JSONResponse(workflow_state.model_dump())

    @router.post("/runs/{run_id}/approval")
    async def run_approval_endpoint(run_id: str, payload: ApprovalRequest) -> JSONResponse:
        """Record a human approval decision and resume the workflow."""
        workflow_state = container.workflow_store.load(run_id)
        if not workflow_state:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
        await container.workflow_engine.record_human_decision(run_id, payload.decision)
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
            with container.feedback_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception:
            logger.exception("failed to persist feedback", extra={"run_id": run_id})
            return JSONResponse(
                {"status": "error", "message": "Unable to record feedback."},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        _log("feedback stored", run_id)
        return JSONResponse({"status": "recorded"}, status_code=status.HTTP_201_CREATED)

    return router


def router_from_request(request: Request) -> APIRouter:
    """Back-compat for callers that need a router but have a FastAPI request.

    Prefer wiring routes during app construction with `get_router(container)`.
    """

    container = _require_container(request)
    return get_router(container)


_LEGACY_CONTAINER: "BackendContainer | None" = None


def _get_legacy_container() -> "BackendContainer":
    """Lazy container for legacy imports.

    Preserves older patterns like `from app.api import STATE_STORE` without
    triggering initialization at module import time.
    """

    global _LEGACY_CONTAINER
    if _LEGACY_CONTAINER is not None:
        return _LEGACY_CONTAINER

    settings = get_settings()

    from .container import build_container, startup, wire_legacy_globals

    container = build_container(settings=settings)
    wire_legacy_globals(container)
    startup(container)
    _LEGACY_CONTAINER = container
    return container


def _require_container(request: Request) -> "BackendContainer":
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="backend container not initialized",
        )
    return container


def __getattr__(name: str) -> Any:  # pragma: no cover - compatibility shim
    import warnings

    legacy_map = {
        "EVENT_STORE": "event_store",
        "EVENT_BUS": "event_bus",
        "STATE_STORE": "state_store",
        "WORKFLOW_STORE": "workflow_store",
        "WORKFLOW_ENGINE": "workflow_engine",
        "RUN_COORDINATOR": "run_coordinator",
        "TRACE_STORE": "trace_store",
        "TRACER": "tracer",
        "RETRIEVAL_STORE": "retrieval_store",
        "EMBEDDING_GENERATOR": "embedding_generator",
        "MCP_REGISTRY": "mcp_registry",
        "MCP_CLIENT": "mcp_client",
        "PERMISSION_GATE": "permission_gate",
        "CACHE_STORE": "cache_store",
        "RATE_LIMITER": "rate_limiter",
        "BUDGET_MANAGER": "budget_manager",
        "GUARDRAIL_MONITOR": "guardrail_monitor",
        "router": None,
    }
    if name not in legacy_map:
        raise AttributeError(name)
    warnings.warn(
        f"`app.api.{name}` is deprecated; use dependency injection via `BackendContainer` (see `app.container`).",
        DeprecationWarning,
        stacklevel=2,
    )
    container = _get_legacy_container()
    attr = legacy_map[name]
    if attr is None:
        return get_router(container)
    return getattr(container, attr)
