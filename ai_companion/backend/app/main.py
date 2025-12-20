"""FastAPI application bootstrap for Session 0 backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router


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

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
