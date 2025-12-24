"""Registry that stores MCP tool descriptors and server metadata."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from .schema import ToolDescriptor
from .server import MCPServer


class MCPRegistry:
    """In-memory mapping of tool descriptors grouped by server."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        self._tool_servers: dict[str, str] = {}
        self._server_tools: dict[str, set[str]] = defaultdict(set)
        self._servers: dict[str, MCPServer] = {}

    def register_server(self, server: MCPServer) -> None:
        """Track a server for tool refresh and routing."""
        self._servers[server.server_id] = server
        self._server_tools.setdefault(server.server_id, set())

    def remove_server(self, server_id: str) -> None:
        """Detach a server and purge its tools."""
        if server_id in self._servers:
            self._servers.pop(server_id, None)
        tool_names = self._server_tools.pop(server_id, set())
        for name in tool_names:
            self._tools.pop(name, None)
            self._tool_servers.pop(name, None)

    def list_servers(self) -> list[MCPServer]:
        return list(self._servers.values())

    def list_tools(self) -> list[ToolDescriptor]:
        """Return all registered tool descriptors."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> ToolDescriptor | None:
        """Return the descriptor for the requested tool."""
        return self._tools.get(name)

    def get_server_for_tool(self, name: str) -> MCPServer | None:
        """Resolve the MCP server that manages the given tool."""
        server_id = self._tool_servers.get(name)
        if not server_id:
            return None
        return self._servers.get(server_id)

    def refresh_tools(self, server: MCPServer, tools: Iterable[ToolDescriptor]) -> None:
        """Replace the tools associated with a server."""
        server_id = server.server_id
        existing = self._server_tools.get(server_id, set())
        for name in list(existing):
            self._tools.pop(name, None)
            self._tool_servers.pop(name, None)
        self._server_tools[server_id] = set()

        for descriptor in tools:
            self._tools[descriptor.name] = descriptor
            self._tool_servers[descriptor.name] = server_id
            self._server_tools[server_id].add(descriptor.name)

    def describe(self) -> Mapping[str, ToolDescriptor]:
        """Return a mapping of tool name to descriptor (mainly for diagnostics)."""
        return dict(self._tools)
