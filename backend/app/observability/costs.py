"""Cost configuration helpers for model spans."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ModelCost:
    """Represents configured pricing for a model."""

    model_name: str
    input_token_usd: float
    output_token_usd: float


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _slugify(model_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", model_name or "")
    return slug.strip("_").upper() or "DEFAULT"


@lru_cache(maxsize=64)
def _load_cost_config(model_name: str) -> ModelCost:
    slug = _slugify(model_name)
    default_input = _env_float("MODEL_PRICE_DEFAULT_INPUT_USD", 0.0)
    default_output = _env_float("MODEL_PRICE_DEFAULT_OUTPUT_USD", 0.0)
    input_price = _env_float(f"MODEL_PRICE_{slug}_INPUT_USD", default_input)
    output_price = _env_float(f"MODEL_PRICE_{slug}_OUTPUT_USD", default_output)
    return ModelCost(
        model_name=model_name,
        input_token_usd=max(input_price, 0.0),
        output_token_usd=max(output_price, 0.0),
    )


def estimate_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a model invocation."""
    config = _load_cost_config(model_name)
    prompt_cost = input_tokens * config.input_token_usd
    completion_cost = output_tokens * config.output_token_usd
    total = prompt_cost + completion_cost
    return round(total, 6)
