"""FastAPI application bootstrap for Session 5 backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import EMBEDDING_GENERATOR, EVENT_BUS, RETRIEVAL_STORE, router
from .executor import ToolExecutor
from .ingestion import run_ingestion
from .tools import get_tool_registry


class _RunIdFilter(logging.Filter):
    """Ensure every log record has a run_id attribute."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            record.run_id = "system"
        return True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [run_id=%(run_id)s] %(name)s: %(message)s",
    )
    root_logger = logging.getLogger()
    run_filter = _RunIdFilter()
    for handler in root_logger.handlers:
        handler.addFilter(run_filter)


_configure_logging()

TOOL_EXECUTOR = ToolExecutor(EVENT_BUS, get_tool_registry())


def create_app() -> FastAPI:
    """Construct the FastAPI application."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.on_event("startup")
    async def _startup() -> None:
        await TOOL_EXECUTOR.start()
        stats = await run_ingestion(
            RETRIEVAL_STORE,
            embedder=EMBEDDING_GENERATOR,
            event_bus=EVENT_BUS,
        )
        logger = logging.getLogger(__name__)
        logger.info(
            "knowledge ingestion ready documents=%s chunks=%s",
            stats.get("documents_ingested"),
            stats.get("chunks_indexed"),
            extra={"run_id": "system"},
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await TOOL_EXECUTOR.shutdown()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
