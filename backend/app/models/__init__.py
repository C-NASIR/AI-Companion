"""Model routing package."""

from .router import ModelCapability, ModelRouter, get_model_router

__all__ = ["ModelCapability", "ModelRouter", "get_model_router", "MODEL_ROUTER"]


def __getattr__(name: str):  # pragma: no cover
    if name == "MODEL_ROUTER":
        return get_model_router()
    raise AttributeError(name)
