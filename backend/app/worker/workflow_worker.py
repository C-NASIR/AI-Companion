"""Workflow runner worker for distributed mode.

Listens for run events via the shared event transport and drives workflows.

This worker owns:
- RunCoordinator subscriptions
- WorkflowEngine runtime tasks

Tools are executed by the separate tool worker (Phase 3).
"""

from __future__ import annotations

import asyncio
import logging

from ..container import (
    build_container,
    shutdown as shutdown_container,
    startup as startup_container,
)
from ..env import load_dotenv_if_present
from ..ingestion import run_ingestion
from ..mcp.bootstrap import initialize_mcp
from ..settings import get_settings

logger = logging.getLogger(__name__)


async def run_workflow_worker() -> None:
    load_dotenv_if_present()
    settings = get_settings()
    if settings.runtime.mode != "distributed":
        raise RuntimeError("workflow worker requires BACKEND_MODE=distributed")

    container = build_container(settings=settings, start_workflow_on_run_start=True)
    # Prepare stores + event bus, but delay subscriptions until after ingestion is ready.
    startup_container(container, start_coordinator=False, start_guardrail_monitor=False)
    await initialize_mcp(container)

    stats = await run_ingestion(
        container.retrieval_store,
        embedder=container.embedding_generator,
        event_bus=container.event_bus,
    )
    logger.info(
        "knowledge ingestion ready documents=%s chunks=%s",
        stats.get("documents_ingested"),
        stats.get("chunks_indexed"),
        extra={"run_id": "system"},
    )

    container.run_coordinator.start()
    logger.info("workflow worker started", extra={"run_id": "system"})
    try:
        # All work happens via EventBus subscriptions; keep process alive.
        await asyncio.Event().wait()
    finally:
        await shutdown_container(container)


def main() -> None:
    asyncio.run(run_workflow_worker())


if __name__ == "__main__":  # pragma: no cover
    main()

