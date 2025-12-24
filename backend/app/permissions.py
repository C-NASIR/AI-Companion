"""Permission enforcement for MCP tool usage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

from .mcp.schema import ToolDescriptor


@dataclass(frozen=True)
class PermissionContext:
    """Attributes used to evaluate whether a tool scope is allowed."""

    user_role: str
    environment: str
    run_type: str


class PermissionGate:
    """Centralized rule evaluation for MCP permission scopes."""

    def __init__(self, environment: str | None = None):
        self.environment = environment or os.getenv("APP_ENV", "development")

    def build_context(self, *, user_role: str, run_type: str) -> PermissionContext:
        return PermissionContext(
            user_role=user_role,
            environment=self.environment,
            run_type=run_type,
        )

    def is_allowed(
        self, scope: str, context: PermissionContext
    ) -> tuple[bool, str | None]:
        """Return (allowed, reason) for the provided scope."""
        if scope.startswith("calculator."):
            return True, None
        if scope == "github.read":
            if context.environment == "development":
                return True, None
            return False, "scope_not_allowed_environment"
        return False, "scope_not_allowed"

    def filter_allowed(
        self, descriptors: Sequence[ToolDescriptor], context: PermissionContext
    ) -> list[ToolDescriptor]:
        """Return only the tools permitted in the provided context."""
        allowed: list[ToolDescriptor] = []
        for descriptor in descriptors:
            permitted, _ = self.is_allowed(descriptor.permission_scope, context)
            if permitted:
                allowed.append(descriptor)
        return allowed
