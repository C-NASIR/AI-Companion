"""FastAPI application bootstrap for Session 6 backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    EMBEDDING_GENERATOR,
    EVENT_BUS,
    CACHE_STORE,
    GUARDRAIL_MONITOR,
    MCP_CLIENT,
    MCP_REGISTRY,
    PERMISSION_GATE,
    RETRIEVAL_STORE,
    RUN_COORDINATOR,
    STATE_STORE,
    TRACER,
    router,
)
from .executor import ToolExecutor
from .ingestion import run_ingestion
from .events import tool_discovered_event
from .mcp.servers.calculator_server import CalculatorMCPServer
from .mcp.servers.github_server import GitHubMCPServer
from .settings import settings
from .startup_checks import run_startup_checks


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
run_startup_checks()

TOOL_EXECUTOR = ToolExecutor(
    EVENT_BUS,
    MCP_REGISTRY,
    MCP_CLIENT,
    PERMISSION_GATE,
    STATE_STORE,
    TRACER,
    tool_firewall_enabled=settings.guardrails.tool_firewall_enabled,
    cache_store=CACHE_STORE,
    tool_cache_enabled=settings.caching.tool_cache_enabled,
)
_MCP_INITIALIZED = False


async def _initialize_mcp() -> None:
    global _MCP_INITIALIZED
    if _MCP_INITIALIZED:
        return
    servers = [CalculatorMCPServer(), GitHubMCPServer()]
    for server in servers:
        MCP_CLIENT.register_server(server)
    descriptors = await MCP_CLIENT.discover_tools()
    for descriptor in descriptors:
        await EVENT_BUS.publish(
            tool_discovered_event(
                "system",
                tool_name=descriptor.name,
                source=descriptor.source,
                permission_scope=descriptor.permission_scope,
            )
        )
    _MCP_INITIALIZED = True


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
        await _initialize_mcp()
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
        await RUN_COORDINATOR.shutdown()
        GUARDRAIL_MONITOR.close()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
