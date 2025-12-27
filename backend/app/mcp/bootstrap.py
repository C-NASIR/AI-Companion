"""MCP bootstrap helpers shared by web and worker processes."""

from __future__ import annotations

from ..events import tool_discovered_event
from .servers.calculator_server import CalculatorMCPServer
from .servers.github_server import GitHubMCPServer


async def initialize_mcp(container) -> None:
    """Register MCP servers, discover tools, and emit tool.discovered events."""

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

