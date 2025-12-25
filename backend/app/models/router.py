"""Environment-driven model routing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class ModelCapability(str, Enum):
    """Supported high-level model intents."""

    PLANNING = "planning"
    GENERATION = "generation"
    VERIFICATION = "verification"
    CLASSIFICATION = "classification"


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip()
    return normalized or default


@dataclass
class RoutingConfig:
    """Resolved routing configuration."""

    default_model: str
    overrides: dict[ModelCapability, str] = field(default_factory=dict)


class ModelRouter:
    """Resolves capabilities to concrete model IDs."""

    def __init__(self) -> None:
        self._config = self._load_config()

    def _load_config(self) -> RoutingConfig:
        default_model = _env_str("MODEL_ROUTING_DEFAULT_MODEL") or _env_str(
            "OPENAI_MODEL", "gpt-4o-mini"
        )
        overrides: dict[ModelCapability, str] = {}
        for capability in ModelCapability:
            env_name = f"MODEL_ROUTING_{capability.name}_MODEL"
            model_name = _env_str(env_name)
            if model_name:
                overrides[capability] = model_name
        return RoutingConfig(default_model=default_model, overrides=overrides)

    def route(self, capability: ModelCapability) -> str:
        """Return the configured model for a capability."""
        return self._config.overrides.get(capability) or self._config.default_model

    def describe(self) -> dict[str, str]:
        """Return a mapping for diagnostics."""
        return {capability.value: self.route(capability) for capability in ModelCapability}

    def reload(self) -> None:
        """Refresh routing from environment variables."""
        self._config = self._load_config()


MODEL_ROUTER = ModelRouter()
