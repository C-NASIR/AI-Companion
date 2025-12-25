"""Run state and decision tracking models for the intelligence layer."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import ChatMode, iso_timestamp


class DecisionRecord(BaseModel):
    """Structured entry describing a single decision made during a run."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str
    ts: str = Field(default_factory=iso_timestamp)
    notes: str | None = None


class PlanType(str, Enum):
    """High-level plan classification choices."""

    DIRECT_ANSWER = "direct_answer"
    NEEDS_CLARIFICATION = "needs_clarification"
    CANNOT_ANSWER = "cannot_answer"


class RunPhase(str, Enum):
    """Named phases for the fixed intelligence control graph."""

    INIT = "init"
    RECEIVE = "receive"
    PLAN = "plan"
    RETRIEVE = "retrieve"
    RESPOND = "respond"
    WAITING_FOR_TOOL = "waiting_for_tool"
    VERIFY = "verify"
    APPROVAL = "approval"
    FINALIZE = "finalize"


class AvailableToolRecord(BaseModel):
    """Metadata stored for tools available during a run."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: str
    permission_scope: str
    server_id: str | None = None


class ToolRequestRecord(BaseModel):
    """Recorded intent for a tool invocation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any]
    ts: str = Field(default_factory=iso_timestamp)


class ToolResultRecord(BaseModel):
    """Structured record for the outcome of a tool invocation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: int | None = None
    ts: str = Field(default_factory=iso_timestamp)

    def model_post_init(self, __context: Any) -> None:
        if self.status not in ("completed", "failed"):
            msg = f"invalid tool result status={self.status}"
            raise ValueError(msg)
        if self.status == "completed" and not isinstance(self.output, dict):
            raise ValueError("completed tool results require output data")
        if self.status == "failed" and not isinstance(self.error, dict):
            raise ValueError("failed tool results require error data")


class RunState(BaseModel):
    """Mutable run state that flows through each intelligence node."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    message: str
    context: str | None = None
    mode: ChatMode
    is_evaluation: bool = False
    tenant_id: str = "default"
    user_id: str = "anonymous"
    cost_limit_usd: float | None = None
    cost_spent_usd: float = 0.0
    degraded: bool = False
    degraded_reason: str | None = None
    phase: RunPhase = Field(default=RunPhase.INIT)
    plan_type: PlanType | None = None
    verification_passed: bool | None = None
    verification_reason: str | None = None
    outcome: str | None = None
    outcome_reason: str | None = None
    output_text: str = ""
    created_at: str = Field(default_factory=iso_timestamp)
    updated_at: str = Field(default_factory=iso_timestamp)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    tool_requests: list[ToolRequestRecord] = Field(default_factory=list)
    tool_results: list[ToolResultRecord] = Field(default_factory=list)
    last_tool_status: str | None = None
    available_tools: list[AvailableToolRecord] = Field(default_factory=list)
    requested_tool: str | None = None
    tool_source: str | None = None
    tool_permission_scope: str | None = None
    tool_denied_reason: str | None = None
    retrieved_chunks: list["RetrievedChunkRecord"] = Field(default_factory=list)
    sanitized_chunk_ids: list[str] = Field(default_factory=list)
    guardrail_status: str | None = None
    guardrail_reason: str | None = None
    guardrail_layer: str | None = None
    guardrail_threat_type: str | None = None

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        """Ensure every run has a stable identifier for logging."""
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("run_id must be a non-empty string")
        return normalized

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        message: str,
        context: str | None,
        mode: ChatMode,
        is_evaluation: bool = False,
        tenant_id: str = "default",
        user_id: str = "anonymous",
        cost_limit_usd: float | None = None,
    ) -> "RunState":
        """Create a new RunState instance with synchronized timestamps."""
        ts = iso_timestamp()
        tenant = (tenant_id or "default").strip() or "default"
        user = (user_id or "anonymous").strip() or "anonymous"
        return cls(
            run_id=run_id,
            message=message,
            context=context,
            mode=mode,
            is_evaluation=is_evaluation,
            tenant_id=tenant,
            user_id=user,
            cost_limit_usd=cost_limit_usd,
            phase=RunPhase.INIT,
            created_at=ts,
            updated_at=ts,
        )

    def _touch(self) -> None:
        """Refresh updated_at timestamp."""
        self.updated_at = iso_timestamp()

    def log_extra(self) -> dict[str, str]:
        """Return a logging extra payload that enforces run_id tagging."""
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
        }

    def record_model_cost(self, amount_usd: float) -> float:
        """Track cumulative model spend."""
        if amount_usd:
            self.cost_spent_usd = max(self.cost_spent_usd + amount_usd, 0.0)
            self._touch()
        return self.cost_spent_usd

    def mark_degraded(self, reason: str) -> bool:
        """Set degraded mode for the run."""
        was_degraded = self.degraded
        self.degraded = True
        self.degraded_reason = reason
        self._touch()
        return not was_degraded

    def transition_phase(self, new_phase: RunPhase) -> None:
        """Move the run into a new phase."""
        self.phase = new_phase
        self._touch()

    def append_output(self, text: str) -> None:
        """Append generated text to the accumulated output buffer."""
        if not text:
            return
        self.output_text += text
        self._touch()

    def record_decision(self, name: str, value: str, notes: str | None = None) -> None:
        """Store a decision entry and update the timestamp."""
        self.decisions.append(DecisionRecord(name=name, value=value, notes=notes))
        self._touch()

    def set_available_tools(
        self, tools: Sequence[AvailableToolRecord] | Sequence[Mapping[str, Any]]
    ) -> None:
        """Persist normalized metadata for allowed tools."""
        normalized: list[AvailableToolRecord] = []
        for tool in tools:
            if isinstance(tool, AvailableToolRecord):
                normalized.append(tool)
                continue
            if isinstance(tool, Mapping):
                name = str(tool.get("name") or "")
                source = str(tool.get("source") or "")
                scope = str(tool.get("permission_scope") or "")
                server_id = tool.get("server_id")
            else:
                name = getattr(tool, "name", "")
                source = getattr(tool, "source", "")
                scope = getattr(tool, "permission_scope", "")
                server_id = getattr(tool, "server_id", None)
            normalized.append(
                AvailableToolRecord(
                    name=name,
                    source=source,
                    permission_scope=scope,
                    server_id=str(server_id) if server_id is not None else None,
                )
            )
        self.available_tools = normalized
        self._touch()

    def set_tool_context(
        self,
        *,
        name: str,
        source: str | None,
        permission_scope: str | None,
    ) -> None:
        """Store metadata about the active tool request."""
        self.requested_tool = name
        if source:
            self.tool_source = source
        if permission_scope:
            self.tool_permission_scope = permission_scope
        self.tool_denied_reason = None
        self._touch()

    def record_tool_request(
        self,
        *,
        name: str,
        arguments: Mapping[str, Any],
        status: str = "requested",
        source: str | None = None,
        permission_scope: str | None = None,
    ) -> None:
        """Persist metadata for a requested tool invocation."""
        self.tool_requests.append(
            ToolRequestRecord(name=name, arguments=dict(arguments))
        )
        self.set_tool_context(name=name, source=source, permission_scope=permission_scope)
        self.last_tool_status = status
        self._touch()

    def record_tool_result(
        self,
        *,
        name: str,
        status: str,
        payload: Mapping[str, Any],
        duration_ms: int | None,
    ) -> None:
        """Persist tool execution results."""
        if status not in {"completed", "failed"}:
            msg = f"invalid tool status {status}"
            raise ValueError(msg)
        record_kwargs: dict[str, Any] = {
            "name": name,
            "status": status,
            "duration_ms": duration_ms,
        }
        if status == "completed":
            record_kwargs["output"] = dict(payload)
            record_kwargs["error"] = None
            self.tool_denied_reason = None
        else:
            record_kwargs["error"] = dict(payload)
            record_kwargs["output"] = None
        self.tool_results.append(ToolResultRecord(**record_kwargs))
        self.last_tool_status = status
        self._touch()

    def set_tool_denied(self, reason: str) -> None:
        """Record that a tool request was denied."""
        self.tool_denied_reason = reason
        self.last_tool_status = "denied"
        self._touch()

    def set_plan_type(self, plan_type: PlanType) -> None:
        """Assign the plan type for later stages."""
        self.plan_type = plan_type
        self._touch()

    def set_verification(
        self, *, passed: bool, reason: str | None = None
    ) -> None:
        """Capture verification results for the run."""
        self.verification_passed = passed
        self.verification_reason = reason
        self._touch()

    def set_outcome(self, outcome: str, reason: str | None = None) -> None:
        """Record the final outcome for the run."""
        self.outcome = outcome
        self.outcome_reason = reason
        self._touch()

    def set_retrieved_chunks(
        self, chunks: Sequence["RetrievedChunkRecord"] | Sequence[Mapping[str, Any]]
    ) -> None:
        """Persist retrieval results as structured records."""
        normalized: list[RetrievedChunkRecord] = []
        for chunk in chunks:
            if isinstance(chunk, RetrievedChunkRecord):
                normalized.append(chunk)
                continue
            if isinstance(chunk, Mapping):
                chunk_id = chunk.get("chunk_id")
                document_id = chunk.get("document_id")
                text = chunk.get("text")
                score = chunk.get("score")
                metadata = chunk.get("metadata")
            else:
                chunk_id = getattr(chunk, "chunk_id", "")
                document_id = getattr(chunk, "document_id", "")
                text = getattr(chunk, "text", "")
                score = getattr(chunk, "score", 0.0)
                metadata = getattr(chunk, "metadata", {})
            metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
            normalized.append(
                RetrievedChunkRecord(
                    chunk_id=str(chunk_id),
                    document_id=str(document_id),
                    text=str(text or ""),
                    score=float(score or 0.0),
                    metadata=metadata_dict,
                )
            )
        self.retrieved_chunks = normalized
        self._touch()

    def record_sanitized_chunk(self, chunk_id: str) -> None:
        """Track sanitized retrieval chunks."""
        if not chunk_id:
            return
        if chunk_id not in self.sanitized_chunk_ids:
            self.sanitized_chunk_ids.append(chunk_id)
            self._touch()

    def set_guardrail_status(
        self,
        status: str,
        *,
        reason: str | None = None,
        layer: str | None = None,
        threat_type: str | None = None,
    ) -> None:
        """Persist guardrail status metadata for observability/UI."""
        self.guardrail_status = status
        self.guardrail_reason = reason
        self.guardrail_layer = layer
        self.guardrail_threat_type = threat_type
        self._touch()


class RetrievedChunkRecord(BaseModel):
    """Stored representation of retrieved chunk metadata."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    text: str
    score: float
    metadata: dict[str, Any]
