"""MCP client that coordinates tool discovery and execution."""

from __future__ import annotations

import logging
from typing import Mapping

from .registry import MCPRegistry
from .schema import ToolCallResult, ToolDescriptor
from .server import MCPServer

logger = logging.getLogger(__name__)


class MCPClient:
    """Discovers tools from servers and routes execution requests."""

    def __init__(self, registry: MCPRegistry):
        self.registry = registry

    def register_server(self, server: MCPServer) -> None:
        """Register a server so its tools can be discovered."""
        self.registry.register_server(server)
        logger.info(
            "mcp server registered server_id=%s source=%s",
            server.server_id,
            getattr(server, "source", "unknown"),
            extra={"run_id": "system"},
        )

    async def discover_tools(self) -> list[ToolDescriptor]:
        """Fetch tool descriptors from all known servers."""
        discovered: list[ToolDescriptor] = []
        for server in self.registry.list_servers():
            try:
                descriptors = list(await server.list_tools())
            except Exception:
                logger.exception(
                    "failed to list tools server=%s",
                    server.server_id,
                    extra={"run_id": "system"},
                )
                continue
            self.registry.refresh_tools(server, descriptors)
            discovered.extend(descriptors)
            logger.info(
                "mcp server refreshed server_id=%s tools=%s",
                server.server_id,
                [descriptor.name for descriptor in descriptors],
                extra={"run_id": "system"},
            )
        return discovered

    async def execute_tool(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolCallResult:
        """Route execution to the server responsible for the tool."""
        descriptor = self.registry.get_tool(tool_name)
        if not descriptor:
            raise ValueError(f"unknown tool requested: {tool_name}")
        server = self.registry.get_server_for_tool(tool_name)
        if not server:
            raise ValueError(f"tool {tool_name} is not associated with an active server")
        return await server.call_tool(tool_name=tool_name, arguments=arguments)
