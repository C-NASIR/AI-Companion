"""Startup validation to keep deployments predictable."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
WRITABLE_DIRS = [DATA_DIR, DATA_DIR / "events", DATA_DIR / "state", DATA_DIR / "traces"]
REQUIRED_ENV_VARS = (
    "MODEL_ROUTING_DEFAULT_MODEL",
    "MODEL_PRICE_DEFAULT_INPUT_USD",
    "MODEL_PRICE_DEFAULT_OUTPUT_USD",
    "RATE_LIMIT_GLOBAL_CONCURRENCY",
    "RATE_LIMIT_TENANT_CONCURRENCY",
    "RUN_MODEL_BUDGET_USD",
)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _ensure_positive_int(name: str) -> None:
    raw = _require_env(name)
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be non-negative, got {parsed}")


def _ensure_dir_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    test_file = path / ".startup_write_test"
    try:
        with test_file.open("w", encoding="utf-8") as handle:
            handle.write("ok")
    except OSError as exc:
        raise RuntimeError(f"Directory {path} is not writable: {exc}") from exc
    finally:
        if test_file.exists():
            try:
                test_file.unlink()
            except OSError:
                pass


def run_startup_checks() -> None:
    """Fail fast when configuration or filesystem are invalid."""
    if os.getenv("SKIP_STARTUP_CHECKS") == "1":
        logger.warning("Startup checks skipped via SKIP_STARTUP_CHECKS=1")
        return

    backend_mode = (os.getenv("BACKEND_MODE") or "single_process").strip().lower()
    if backend_mode not in {"single_process", "distributed"}:
        raise RuntimeError(f"BACKEND_MODE must be single_process|distributed, got {backend_mode!r}")

    for var in ("MODEL_ROUTING_DEFAULT_MODEL",):
        _require_env(var)
    for var in (
        "MODEL_PRICE_DEFAULT_INPUT_USD",
        "MODEL_PRICE_DEFAULT_OUTPUT_USD",
        "RUN_MODEL_BUDGET_USD",
    ):
        value = _require_env(var)
        try:
            float(value)
        except ValueError as exc:
            raise RuntimeError(f"{var} must be numeric, got {value!r}") from exc

    for var in ("RATE_LIMIT_GLOBAL_CONCURRENCY", "RATE_LIMIT_TENANT_CONCURRENCY"):
        _ensure_positive_int(var)

    if backend_mode == "single_process":
        for directory in WRITABLE_DIRS:
            _ensure_dir_writable(directory)
    else:
        redis_url = os.getenv("REDIS_URL")
        if redis_url is None or not redis_url.strip():
            raise RuntimeError("REDIS_URL is required when BACKEND_MODE=distributed")
        try:
            from redis import Redis

            client = Redis.from_url(redis_url.strip(), decode_responses=True)
            client.ping()
        except Exception as exc:
            raise RuntimeError(f"Unable to connect to REDIS_URL={redis_url!r}: {exc}") from exc

    logger.info(
        "Startup checks passed. Environment and runtime dependencies are valid.",
        extra={"run_id": "system"},
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        run_startup_checks()
    except Exception as exc:  # pragma: no cover - CLI guard
        logger.error("Startup check failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
