"""Inference subsystem init."""

from .catalog import FallbackChainSpec, ModelCapability, ModelSpec, get_chain
from .engine import ChainExhaustedError, ExecutionResult, execute_with_fallback

__all__ = [
    "ChainExhaustedError",
    "ExecutionResult",
    "FallbackChainSpec",
    "ModelCapability",
    "ModelSpec",
    "execute_with_fallback",
    "get_chain",
]
