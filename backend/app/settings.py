"""Application-wide settings with guardrail feature flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


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


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip()
    return normalized or default


RuntimeMode = Literal["single_process", "distributed"]


@dataclass(frozen=True)
class RuntimeSettings:
    """Runtime configuration for single vs distributed deployments."""

    mode: RuntimeMode
    redis_url: str | None
    run_lease_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        raw_mode = (_env_str("BACKEND_MODE", "single_process") or "single_process").lower()
        mode: RuntimeMode = "distributed" if raw_mode == "distributed" else "single_process"
        return cls(
            mode=mode,
            redis_url=_env_str("REDIS_URL"),
            run_lease_ttl_seconds=max(5, _env_int("RUN_LEASE_TTL_SECONDS", 30)),
        )


@dataclass(frozen=True)
class GuardrailSettings:
    """Per-layer guardrail feature flags."""

    input_gate_enabled: bool
    context_sanitizer_enabled: bool
    output_validator_enabled: bool
    injection_detector_enabled: bool
    tool_firewall_enabled: bool
    monitor_report_seconds: int

    @classmethod
    def from_env(cls) -> "GuardrailSettings":
        return cls(
            input_gate_enabled=_env_bool("GUARDRAIL_INPUT_ENABLED", True),
            context_sanitizer_enabled=_env_bool(
                "GUARDRAIL_CONTEXT_SANITIZER_ENABLED", True
            ),
            output_validator_enabled=_env_bool(
                "GUARDRAIL_OUTPUT_VALIDATION_ENABLED", True
            ),
            injection_detector_enabled=_env_bool(
                "GUARDRAIL_INJECTION_DETECTOR_ENABLED", True
            ),
            tool_firewall_enabled=_env_bool("GUARDRAIL_TOOL_FIREWALL_ENABLED", True),
            monitor_report_seconds=max(
                30, _env_int("GUARDRAIL_MONITOR_REPORT_SECONDS", 120)
            ),
        )


@dataclass(frozen=True)
class CachingSettings:
    """Feature flags for cache layers."""

    retrieval_cache_enabled: bool
    tool_cache_enabled: bool

    @classmethod
    def from_env(cls) -> "CachingSettings":
        return cls(
            retrieval_cache_enabled=_env_bool("CACHE_RETRIEVAL_ENABLED", True),
            tool_cache_enabled=_env_bool("CACHE_TOOL_RESULTS_ENABLED", True),
        )


@dataclass(frozen=True)
class LimitSettings:
    """Rate limiting and budget controls."""

    global_concurrency: int
    tenant_concurrency: int
    model_budget_usd: float

    @classmethod
    def from_env(cls) -> "LimitSettings":
        return cls(
            global_concurrency=_env_int("RATE_LIMIT_GLOBAL_CONCURRENCY", 8),
            tenant_concurrency=_env_int("RATE_LIMIT_TENANT_CONCURRENCY", 4),
            model_budget_usd=float(os.getenv("RUN_MODEL_BUDGET_USD", "0") or 0),
        )


class Settings:
    """Container for application settings."""

    def __init__(
        self,
        *,
        runtime: RuntimeSettings,
        guardrails: GuardrailSettings,
        caching: CachingSettings,
        limits: LimitSettings,
    ) -> None:
        self.runtime = runtime
        self.guardrails = guardrails
        self.caching = caching
        self.limits = limits

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            runtime=RuntimeSettings.from_env(),
            guardrails=GuardrailSettings.from_env(),
            caching=CachingSettings.from_env(),
            limits=LimitSettings.from_env(),
        )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance built from the current environment."""

    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings.from_env()
    return _SETTINGS


def __getattr__(name: str) -> Settings:  # pragma: no cover
    if name == "settings":
        return get_settings()
    raise AttributeError(name)
