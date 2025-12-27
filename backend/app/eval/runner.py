"""Deterministic evaluation runner that replays the full intelligence stack."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import AsyncIterator, Iterable, Sequence

from ..api import (
    EMBEDDING_GENERATOR,
    EVENT_BUS,
    EVENT_STORE,
    RETRIEVAL_STORE,
    RUN_COORDINATOR,
    STATE_STORE,
    TRACE_STORE,
    WORKFLOW_STORE,
    _get_legacy_container,
)
from ..events import Event
from ..executor import ToolExecutor
from ..ingestion import run_ingestion
from ..mcp.bootstrap import initialize_mcp
from ..state import RunState
from ..workflow import WorkflowStore
from ..workflow.models import WorkflowStatus
from .dataset import EvalCase, EvaluationDataset, load_dataset

logger = logging.getLogger(__name__)

EVAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
CASE_ARTIFACTS_DIR = EVAL_DATA_DIR / "cases"
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class CaseRunResult:
    """Summary of a single evaluation replay."""

    case_id: str
    run_id: str
    event_type: str
    finished_ts: str
    duration_seconds: float
    outcome: str | None
    verification_passed: bool | None
    verification_reason: str | None
    final_text: str
    state_path: str
    events_path: str
    trace_path: str
    notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class EvaluationRunner:
    """Executes the evaluation dataset through the production workflow."""

    def __init__(
        self,
        dataset: EvaluationDataset | None = None,
        *,
        artifacts_dir: Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        workflow_store: WorkflowStore | None = None,
    ):
        self.dataset = dataset or load_dataset()
        self.artifacts_dir = Path(artifacts_dir or EVAL_DATA_DIR)
        self.cases_dir = self.artifacts_dir / "cases"
        self.timeout_seconds = timeout_seconds
        self.workflow_store = workflow_store or WORKFLOW_STORE
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_ready = False
        self._tool_executor: ToolExecutor | None = None
        self._container = None

    async def run_all(self, case_ids: Sequence[str] | None = None) -> list[CaseRunResult]:
        """Run every dataset case (optionally filtered) sequentially."""
        await self._prepare_runtime()
        selected = self._select_cases(case_ids)
        results: list[CaseRunResult] = []
        try:
            for case in selected:
                logger.info("starting evaluation case %s", case.id)
                result = await self._run_case(case)
                results.append(result)
                self._write_artifact(case, result)
        finally:
            await self._shutdown_runtime()
        return results

    async def soak(
        self,
        *,
        iterations: int | None = None,
        duration_seconds: float | None = None,
        case_ids: Sequence[str] | None = None,
    ) -> AsyncIterator[CaseRunResult]:
        """Yield case results repeatedly for long-running soak tests."""
        if iterations is None and duration_seconds is None:
            raise ValueError("Provide iterations and/or duration_seconds")
        selected = list(self._select_cases(case_ids))
        if not selected:
            return
        await self._prepare_runtime()
        start = time.perf_counter()
        loops = 0
        try:
            while True:
                for case in selected:
                    yield await self._run_case(case)
                loops += 1
                if iterations is not None and loops >= iterations:
                    break
                if duration_seconds is not None and (
                    time.perf_counter() - start
                ) >= duration_seconds:
                    break
        finally:
            await self._shutdown_runtime()

    def _select_cases(self, case_ids: Sequence[str] | None) -> Iterable[EvalCase]:
        if not case_ids:
            return list(self.dataset)
        requested = set(case_ids)
        missing = requested - {case.id for case in self.dataset}
        if missing:
            missing_ids = ", ".join(sorted(missing))
            msg = f"unknown evaluation case ids: {missing_ids}"
            raise ValueError(msg)
        return [case for case in self.dataset if case.id in requested]

    async def _prepare_runtime(self) -> None:
        if self._runtime_ready:
            return
        self._container = _get_legacy_container()
        await initialize_mcp(self._container)
        if self._tool_executor is None:
            self._tool_executor = ToolExecutor(
                self._container.event_bus,
                self._container.mcp_registry,
                self._container.mcp_client,
                self._container.permission_gate,
                self._container.state_store,
                self._container.tracer,
                run_lease=self._container.run_lease,
                tool_firewall_enabled=self._container.settings.guardrails.tool_firewall_enabled,
                cache_store=self._container.cache_store,
                tool_cache_enabled=self._container.settings.caching.tool_cache_enabled,
            )
        await self._tool_executor.start()
        await run_ingestion(
            RETRIEVAL_STORE,
            embedder=EMBEDDING_GENERATOR,
            event_bus=EVENT_BUS,
        )
        self._runtime_ready = True

    async def _shutdown_runtime(self) -> None:
        if not self._runtime_ready:
            return
        if self._tool_executor:
            await self._tool_executor.shutdown()
        self._runtime_ready = False

    async def _run_case(self, case: EvalCase) -> CaseRunResult:
        run_id = self._build_run_id(case.id)
        state = RunState.new(
            run_id=run_id,
            message=case.input.message,
            context=case.input.context,
            mode=case.mode,
            is_evaluation=True,
             tenant_id="evaluation",
             user_id=case.id,
        )
        completion_event = asyncio.Event()
        terminal_event: dict[str, Event] = {}

        async def _listener(event: Event) -> None:
            if event.type in {"run.completed", "run.failed"}:
                terminal_event["value"] = event
                completion_event.set()

        unsubscribe = EVENT_BUS.subscribe(run_id, _listener)
        start_time = time.perf_counter()
        try:
            await RUN_COORDINATOR.start_run(state)
            await asyncio.wait_for(completion_event.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            logger.error("evaluation case timed out case=%s run_id=%s", case.id, run_id)
            raise RuntimeError(f"run {run_id} timed out") from exc
        finally:
            unsubscribe()
        duration = time.perf_counter() - start_time
        finished_event = terminal_event.get("value")
        if not finished_event:
            raise RuntimeError(f"run {run_id} finished without terminal event")
        final_state = STATE_STORE.load(run_id)
        if not final_state:
            raise RuntimeError(f"run {run_id} missing persisted state")
        workflow_state = self.workflow_store.load(run_id) if self.workflow_store else None
        self._assert_workflow_terminated(workflow_state, run_id)
        result = CaseRunResult(
            case_id=case.id,
            run_id=run_id,
            event_type=finished_event.type,
            finished_ts=finished_event.ts,
            duration_seconds=duration,
            outcome=final_state.outcome,
            verification_passed=final_state.verification_passed,
            verification_reason=final_state.verification_reason,
            final_text=final_state.output_text,
            state_path=str(self._state_path(run_id)),
            events_path=str(self._events_path(run_id)),
            trace_path=str(self._trace_path(run_id)),
            notes=case.expectations.notes,
        )
        logger.info(
            "evaluation case completed case=%s run_id=%s outcome=%s",
            case.id,
            run_id,
            result.outcome,
        )
        return result

    def _write_artifact(self, case: EvalCase, result: CaseRunResult) -> None:
        case_dir = self.cases_dir / case.id
        case_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "case": {
                "id": case.id,
                "description": case.description,
                "expectations": case.expectations.model_dump(),
            },
            "result": result.to_dict(),
        }
        artifact_path = case_dir / "result.json"
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _build_run_id(self, case_id: str) -> str:
        suffix = uuid.uuid4().hex[:8]
        return f"eval-{case_id}-{suffix}"

    def _state_path(self, run_id: str) -> Path:
        return STATE_STORE.base_dir / f"{run_id}.json"

    def _events_path(self, run_id: str) -> Path:
        return EVENT_STORE.base_dir / f"{run_id}.jsonl"

    def _trace_path(self, run_id: str) -> Path:
        return TRACE_STORE.base_dir / f"{run_id}.json"

    def _assert_workflow_terminated(self, workflow_state, run_id: str) -> None:
        if not workflow_state:
            return
        if workflow_state.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            logger.warning(
                "workflow state not terminal run_id=%s status=%s",
                run_id,
                workflow_state.status.value,
            )


__all__ = ["EvaluationRunner", "CaseRunResult", "EVAL_DATA_DIR"]
