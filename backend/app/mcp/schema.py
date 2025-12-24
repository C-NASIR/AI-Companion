"""Shared MCP schema models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolDescriptor(BaseModel):
    """Structured metadata describing a tool exposed by an MCP server."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    permission_scope: str
    source: str
    server_id: str


class ToolCallRequest(BaseModel):
    """Request envelope sent to a server for a tool invocation."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """Response envelope returned by servers after execution."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_payloads(self) -> "ToolCallResult":
        if self.output is None and self.error is None:
            raise ValueError("tool call result requires output or error payload")
        if self.output is not None and self.error is not None:
            raise ValueError("tool call result cannot include both output and error")
        return self
