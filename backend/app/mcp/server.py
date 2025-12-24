"""Abstract MCP server contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence

from .schema import ToolCallResult, ToolDescriptor


class MCPServerError(Exception):
    """Raised when an MCP server cannot fulfill a request."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


class MCPServer(ABC):
    """Minimal interface all MCP servers must implement."""

    def __init__(self, server_id: str, *, source: str):
        self.server_id = server_id
        self.source = source

    @abstractmethod
    async def list_tools(self) -> Sequence[ToolDescriptor]:
        """Return the tools exposed by this server."""

    @abstractmethod
    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolCallResult:
        """Execute a tool based on the provided input."""
