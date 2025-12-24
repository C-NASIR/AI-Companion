"""GitHub-backed MCP server exposing read-only repo operations."""

from __future__ import annotations

import base64
import os
from typing import Any, Mapping

import httpx

from ..schema import ToolCallResult, ToolDescriptor
from ..server import MCPServer, MCPServerError

LIST_FILES_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "description": "owner/repo identifier"},
        "path": {
            "type": "string",
            "description": "Optional directory path to list (defaults to repo root).",
        },
    },
    "required": ["repo"],
    "additionalProperties": False,
}

LIST_FILES_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["files"],
    "additionalProperties": False,
}

READ_FILE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "description": "owner/repo identifier"},
        "path": {"type": "string", "description": "Path to the file to read"},
    },
    "required": ["repo", "path"],
    "additionalProperties": False,
}

READ_FILE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
    },
    "required": ["content"],
    "additionalProperties": False,
}


class GitHubMCPServer(MCPServer):
    """Read-only GitHub integration implemented via MCP."""

    def __init__(self, token: str | None = None):
        super().__init__("github_server", source="external")
        self.token = token or os.getenv("GITHUB_TOKEN")
        self._descriptors = [
            ToolDescriptor(
                name="github.list_files",
                description="List files within a GitHub repository path.",
                input_schema=LIST_FILES_INPUT_SCHEMA,
                output_schema=LIST_FILES_OUTPUT_SCHEMA,
                permission_scope="github.read",
                source="external",
                server_id=self.server_id,
            ),
            ToolDescriptor(
                name="github.read_file",
                description="Read the content of a GitHub repository file.",
                input_schema=READ_FILE_INPUT_SCHEMA,
                output_schema=READ_FILE_OUTPUT_SCHEMA,
                permission_scope="github.read",
                source="external",
                server_id=self.server_id,
            ),
        ]

    async def list_tools(self):
        return self._descriptors

    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolCallResult:
        if tool_name == "github.list_files":
            return await self._handle_list_files(arguments)
        if tool_name == "github.read_file":
            return await self._handle_read_file(arguments)
        raise ValueError(f"github server does not support tool {tool_name}")

    async def _handle_list_files(self, arguments: Mapping[str, Any]) -> ToolCallResult:
        repo = _get_str(arguments, "repo")
        path = _get_optional_str(arguments, "path")
        if not repo:
            return ToolCallResult(
                tool_name="github.list_files",
                error={"error": "missing_repo"},
            )
        try:
            payload = await self._github_request(repo, path or "")
        except MCPServerError as exc:
            return ToolCallResult(
                tool_name="github.list_files",
                error={"error": "github_error", "details": exc.details or {"message": str(exc)}},
            )
        files: list[str] = []
        if isinstance(payload, list):
            for entry in payload:
                path_value = entry.get("path")
                if isinstance(path_value, str):
                    files.append(path_value)
        elif isinstance(payload, dict):
            path_value = payload.get("path")
            if isinstance(path_value, str):
                files.append(path_value)
        else:
            files = []
        return ToolCallResult(
            tool_name="github.list_files",
            output={"files": files},
        )

    async def _handle_read_file(self, arguments: Mapping[str, Any]) -> ToolCallResult:
        repo = _get_str(arguments, "repo")
        path = _get_str(arguments, "path")
        if not repo or not path:
            return ToolCallResult(
                tool_name="github.read_file",
                error={"error": "missing_arguments"},
            )
        try:
            payload = await self._github_request(repo, path)
        except MCPServerError as exc:
            return ToolCallResult(
                tool_name="github.read_file",
                error={"error": "github_error", "details": exc.details or {"message": str(exc)}},
            )
        if not isinstance(payload, dict) or payload.get("type") != "file":
            return ToolCallResult(
                tool_name="github.read_file",
                error={"error": "not_a_file"},
            )
        raw_content = payload.get("content")
        if not isinstance(raw_content, str):
            return ToolCallResult(
                tool_name="github.read_file",
                error={"error": "missing_content"},
            )
        encoding = payload.get("encoding")
        if encoding != "base64":
            return ToolCallResult(
                tool_name="github.read_file",
                error={"error": "unsupported_encoding"},
            )
        decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
        return ToolCallResult(
            tool_name="github.read_file",
            output={"content": decoded},
        )

    async def _github_request(self, repo: str, path: str) -> Any:
        if not self.token:
            raise MCPServerError("missing_token", details={"error": "missing_token"})
        normalized_path = path.lstrip("/")
        url = f"https://api.github.com/repos/{repo}/contents/{normalized_path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-companion-mcp",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise MCPServerError(
                "github_api_error",
                details={
                    "status": response.status_code,
                    "body": response.text[:200],
                },
            )
        return response.json()


def _get_str(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _get_optional_str(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return value if isinstance(value, str) and value.strip() else None
