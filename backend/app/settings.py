"""Application-wide settings with guardrail feature flags."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class GuardrailSettings:
    """Per-layer guardrail feature flags."""

    input_gate_enabled: bool = _env_bool("GUARDRAIL_INPUT_ENABLED", True)
    context_sanitizer_enabled: bool = _env_bool(
        "GUARDRAIL_CONTEXT_SANITIZER_ENABLED", True
    )
    output_validator_enabled: bool = _env_bool(
        "GUARDRAIL_OUTPUT_VALIDATION_ENABLED", True
    )
    injection_detector_enabled: bool = _env_bool(
        "GUARDRAIL_INJECTION_DETECTOR_ENABLED", True
    )
    tool_firewall_enabled: bool = _env_bool("GUARDRAIL_TOOL_FIREWALL_ENABLED", True)
    monitor_report_seconds: int = max(
        30, _env_int("GUARDRAIL_MONITOR_REPORT_SECONDS", 120)
    )


class Settings:
    """Container for application settings."""

    def __init__(self) -> None:
        self.guardrails = GuardrailSettings()


settings = Settings()
