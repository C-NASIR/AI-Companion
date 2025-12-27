"""FastAPI application bootstrap for Session 6 backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import get_router
from .container import (
    build_container,
    shutdown as shutdown_container,
    startup as startup_container,
    wire_legacy_globals,
)
from .executor import ToolExecutor
from .ingestion import run_ingestion
from .events import tool_discovered_event
from .mcp.servers.calculator_server import CalculatorMCPServer
from .mcp.servers.github_server import GitHubMCPServer
from .startup_checks import run_startup_checks
from .env import load_dotenv_if_present


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


async def _initialize_mcp(container) -> None:
    if getattr(container, "_mcp_initialized", False):
        return
    servers = [CalculatorMCPServer(), GitHubMCPServer()]
    for server in servers:
        container.mcp_client.register_server(server)
    descriptors = await container.mcp_client.discover_tools()
    for descriptor in descriptors:
        await container.event_bus.publish(
            tool_discovered_event(
                "system",
                tool_name=descriptor.name,
                source=descriptor.source,
                permission_scope=descriptor.permission_scope,
            )
        )
    container._mcp_initialized = True


def create_app() -> FastAPI:
    """Construct the FastAPI application."""
    _configure_logging()
    load_dotenv_if_present()

    from .settings import get_settings

    settings = get_settings()

    container = build_container(settings=settings)
    wire_legacy_globals(container)

    tool_executor = ToolExecutor(
        container.event_bus,
        container.mcp_registry,
        container.mcp_client,
        container.permission_gate,
        container.state_store,
        container.tracer,
        tool_firewall_enabled=settings.guardrails.tool_firewall_enabled,
        cache_store=container.cache_store,
        tool_cache_enabled=settings.caching.tool_cache_enabled,
    )

    app = FastAPI()
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(get_router(container))

    @app.on_event("startup")
    async def _startup() -> None:
        run_startup_checks()
        startup_container(container)
        await _initialize_mcp(container)
        await tool_executor.start()
        stats = await run_ingestion(
            container.retrieval_store,
            embedder=container.embedding_generator,
            event_bus=container.event_bus,
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
        await tool_executor.shutdown()
        await shutdown_container(container)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


_APP: FastAPI | None = None


def get_app() -> FastAPI:
    """Legacy accessor for ASGI servers expecting an `app` variable."""

    global _APP
    if _APP is None:
        _APP = create_app()
    return _APP


def __getattr__(name: str):  # pragma: no cover
    if name == "app":
        return get_app()
    raise AttributeError(name)
