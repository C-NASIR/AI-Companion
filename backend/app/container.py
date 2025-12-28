"""Explicit dependency container for backend runtime wiring.

This module is intentionally side-effect free on import. It provides functions
to build and lifecycle-manage the backend dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .cache import CacheStore
    from .coordinator import RunCoordinator
    from .events import EventBus, EventStore
    from .guardrails.context_sanitizer import ContextSanitizer
    from .guardrails.input_gate import InputGate
    from .guardrails.injection_detector import InjectionDetector
    from .guardrails.output_validator import OutputValidator
    from .ingestion import EmbeddingGenerator
    from .limits.budget import BudgetManager
    from .limits.rate_limiter import RateLimiter
    from .mcp.client import MCPClient
    from .mcp.registry import MCPRegistry
    from .observability.guardrail_monitor import GuardrailMonitor
    from .observability.store import TraceStore
    from .observability.tracer import Tracer
    from .permissions import PermissionGate
    from .retrieval import InMemoryRetrievalStore
    from .settings import Settings
    from .state_store import StateStore
    from .workflow import ActivityContext, WorkflowEngine, WorkflowStore
    from .lease import RunLease


@dataclass
class BackendContainer:
    """Holds the constructed runtime dependencies for the backend."""

    settings: Settings

    data_dir: Path
    feedback_file: Path
    events_dir: Path
    state_dir: Path
    workflow_dir: Path
    trace_dir: Path

    event_store: EventStore
    event_bus: EventBus
    state_store: StateStore
    workflow_store: WorkflowStore
    trace_store: TraceStore
    tracer: Tracer

    embedding_generator: EmbeddingGenerator
    retrieval_store: InMemoryRetrievalStore

    mcp_registry: MCPRegistry
    permission_gate: PermissionGate
    mcp_client: MCPClient

    cache_store: CacheStore
    rate_limiter: RateLimiter
    budget_manager: BudgetManager

    input_gate: InputGate | None
    context_sanitizer: ContextSanitizer | None
    output_validator: OutputValidator | None
    injection_detector: InjectionDetector | None
    guardrail_monitor: GuardrailMonitor

    activity_context: ActivityContext
    workflow_engine: WorkflowEngine
    run_coordinator: RunCoordinator

    run_lease: RunLease


def build_container(
    *,
    settings: "Settings" | None = None,
    data_dir: Path | None = None,
    start_workflow_on_run_start: bool | None = None,
) -> BackendContainer:
    """Construct the backend dependency graph without starting background work."""

    # Local imports keep this module side-effect-free on import.
    from .cache import CacheStore
    from .coordinator import RunCoordinator
    from .events import EventBus, EventStore
    from .event_transport import (
        InMemoryEventTransport,
        RedisEventTransport,
        RedisEventTransportConfig,
    )
    from .lease import NoopRunLease
    from .tool_queue import NoopToolQueuePublisher
    from .guardrails.context_sanitizer import ContextSanitizer
    from .guardrails.input_gate import InputGate
    from .guardrails.injection_detector import InjectionDetector
    from .guardrails.output_validator import OutputValidator
    from .ingestion import EmbeddingGenerator
    from .limits.budget import BudgetManager
    from .limits.rate_limiter import RateLimiter
    from .mcp.client import MCPClient
    from .mcp.registry import MCPRegistry
    from .observability.guardrail_monitor import GuardrailMonitor
    from .observability.store import TraceStore
    from .observability.tracer import Tracer
    from .permissions import PermissionGate
    from .retrieval import InMemoryRetrievalStore
    from .settings import settings as default_settings
    from .state_store import StateStore
    from .workflow import ActivityContext, WorkflowEngine, WorkflowStore, build_activity_map

    settings = settings or default_settings
    if start_workflow_on_run_start is None:
        start_workflow_on_run_start = settings.runtime.mode == "single_process"

    instance_id = str(uuid4())

    project_root = Path(__file__).resolve().parent.parent
    resolved_data_dir = data_dir or (project_root / "data")
    feedback_file = resolved_data_dir / "feedback.jsonl"
    events_dir = resolved_data_dir / "events"
    state_dir = resolved_data_dir / "state"
    workflow_dir = resolved_data_dir / "workflow"
    trace_dir = resolved_data_dir / "traces"

    state_store = StateStore(state_dir, ensure_dirs=False)
    workflow_store = WorkflowStore(workflow_dir, ensure_dirs=False)
    trace_store = TraceStore(trace_dir, ensure_dirs=False)
    tracer = Tracer(trace_store)

    event_store = EventStore(events_dir, ensure_dirs=False)
    event_transport = InMemoryEventTransport()
    tool_queue = NoopToolQueuePublisher()

    embedding_generator = EmbeddingGenerator()
    retrieval_store = InMemoryRetrievalStore(embedding_generator.embed)

    mcp_registry = MCPRegistry()
    permission_gate = PermissionGate()
    mcp_client = MCPClient(mcp_registry)

    cache_store = CacheStore()
    rate_limiter = RateLimiter(
        settings.limits.global_concurrency, settings.limits.tenant_concurrency
    )
    budget_manager = BudgetManager(settings.limits.model_budget_usd)

    run_lease = NoopRunLease()
    if settings.runtime.mode == "distributed":
        if not settings.runtime.redis_url:
            msg = "BACKEND_MODE=distributed requires REDIS_URL"
            raise ValueError(msg)
        from .distributed.redis_lease import RedisLeaseConfig, RedisRunLease
        from .distributed.redis_stores import (
            RedisEventStore,
            RedisStateStore,
            RedisStoreConfig,
            RedisTraceStore,
            RedisWorkflowStore,
        )

        run_lease = RedisRunLease(
            RedisLeaseConfig(
                url=settings.runtime.redis_url,
                owner_id=instance_id,
                ttl_seconds=settings.runtime.run_lease_ttl_seconds,
            )
        )

        store_config = RedisStoreConfig(url=settings.runtime.redis_url)
        event_store = RedisEventStore(store_config)
        state_store = RedisStateStore(store_config)
        workflow_store = RedisWorkflowStore(store_config)
        trace_store = RedisTraceStore(store_config)
        tracer = Tracer(trace_store)

        event_transport = RedisEventTransport(
            RedisEventTransportConfig(url=settings.runtime.redis_url)
        )

        from .distributed.redis_tool_queue import RedisToolQueue, RedisToolQueueConfig

        tool_queue = RedisToolQueue(RedisToolQueueConfig(url=settings.runtime.redis_url))

    event_bus = EventBus(event_store, transport=event_transport, tool_queue=tool_queue)

    input_gate = InputGate(event_bus) if settings.guardrails.input_gate_enabled else None
    context_sanitizer = (
        ContextSanitizer(event_bus)
        if settings.guardrails.context_sanitizer_enabled
        else None
    )
    output_validator = (
        OutputValidator(event_bus)
        if settings.guardrails.output_validator_enabled
        else None
    )
    injection_detector = (
        InjectionDetector(event_bus)
        if settings.guardrails.injection_detector_enabled
        else None
    )
    guardrail_monitor = GuardrailMonitor(
        event_bus,
        report_interval=settings.guardrails.monitor_report_seconds,
        subscribe=False,
    )

    def _allowed_tools_provider(state):
        context = permission_gate.build_context(
            user_role="human",
            run_type=state.mode.value,
            is_evaluation=state.is_evaluation,
        )
        return permission_gate.filter_allowed(
            mcp_registry.list_tools(),
            context,
        )

    activity_context = ActivityContext(
        event_bus,
        state_store,
        retrieval_store,
        allowed_tools_provider=_allowed_tools_provider,
        tracer=tracer,
        context_sanitizer=context_sanitizer,
        output_validator=output_validator,
        injection_detector=injection_detector,
        cache_store=cache_store,
        retrieval_cache_enabled=settings.caching.retrieval_cache_enabled,
        budget_manager=budget_manager,
    )
    activity_map = build_activity_map(activity_context)
    workflow_engine = WorkflowEngine(
        event_bus,
        workflow_store,
        state_store,
        activities=activity_map,
        activity_context=activity_context,
        tracer=tracer,
        run_lease=run_lease,
    )
    run_coordinator = RunCoordinator(
        event_bus,
        state_store,
        workflow_engine,
        activity_context,
        tracer,
        rate_limiter=rate_limiter,
        budget_manager=budget_manager,
        input_gate=input_gate,
        injection_detector=injection_detector,
        run_lease=run_lease,
        start_workflow_on_run_start=start_workflow_on_run_start,
        subscribe=False,
    )

    return BackendContainer(
        settings=settings,
        data_dir=resolved_data_dir,
        feedback_file=feedback_file,
        events_dir=events_dir,
        state_dir=state_dir,
        workflow_dir=workflow_dir,
        trace_dir=trace_dir,
        event_store=event_store,
        event_bus=event_bus,
        state_store=state_store,
        workflow_store=workflow_store,
        trace_store=trace_store,
        tracer=tracer,
        embedding_generator=embedding_generator,
        retrieval_store=retrieval_store,
        mcp_registry=mcp_registry,
        permission_gate=permission_gate,
        mcp_client=mcp_client,
        cache_store=cache_store,
        rate_limiter=rate_limiter,
        budget_manager=budget_manager,
        input_gate=input_gate,
        context_sanitizer=context_sanitizer,
        output_validator=output_validator,
        injection_detector=injection_detector,
        guardrail_monitor=guardrail_monitor,
        activity_context=activity_context,
        workflow_engine=workflow_engine,
        run_coordinator=run_coordinator,
        run_lease=run_lease,
    )


def wire_legacy_globals(container: BackendContainer) -> None:
    """Bridge for existing modules that rely on configure/get patterns."""

    from .run_logging import configure_state_store
    from .observability.api import configure_trace_api
    from .retrieval import configure_retrieval_store

    configure_state_store(container.state_store)
    configure_retrieval_store(container.retrieval_store)
    configure_trace_api(container.trace_store)


def startup(
    container: BackendContainer,
    *,
    start_coordinator: bool = True,
    start_guardrail_monitor: bool = True,
) -> None:
    """Perform IO-heavy or side-effectful initialization for the container."""

    container.event_store.ensure_base_dir()
    container.state_store.ensure_base_dir()
    container.workflow_store.ensure_base_dir()
    container.trace_store.ensure_base_dir()
    if container.settings.runtime.mode == "single_process":
        container.data_dir.mkdir(parents=True, exist_ok=True)
    if start_coordinator:
        container.run_coordinator.start()
    if start_guardrail_monitor:
        container.guardrail_monitor.start()


async def shutdown(container: BackendContainer) -> None:
    """Shutdown subscriptions/background tasks owned by the container."""

    await container.run_coordinator.shutdown()
    container.guardrail_monitor.close()
    await container.event_bus.close()
    await container.run_lease.close()
