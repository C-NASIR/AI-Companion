"""Read-only FastAPI routes for trace inspection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from .store import TraceNotInitializedError, TraceStore, TraceStoreError


router = APIRouter(tags=["observability"])
_trace_store: TraceStore | None = None


def configure_trace_api(store: TraceStore) -> None:
    """Inject the trace store dependency."""
    global _trace_store
    _trace_store = store


def _require_store() -> TraceStore:
    if not _trace_store:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="trace store not configured",
        )
    return _trace_store


@router.get("/runs/{run_id}/trace")
async def get_run_trace(run_id: str) -> dict[str, Any]:
    """Return the full trace record for the provided run."""
    store = _require_store()
    try:
        trace = store.load_trace(run_id)
    except TraceNotInitializedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="trace not found",
        ) from None
    except TraceStoreError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unable to load trace",
        ) from None
    return trace


@router.get("/runs/{run_id}/spans")
async def get_run_spans(run_id: str) -> list[dict[str, Any]]:
    """Return only the span list for streaming use cases."""
    store = _require_store()
    try:
        spans = store.load_spans(run_id)
    except TraceNotInitializedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="trace not found",
        ) from None
    except TraceStoreError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unable to load spans",
        ) from None
    return spans
