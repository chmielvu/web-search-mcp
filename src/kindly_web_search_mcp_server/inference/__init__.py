"""Inference subsystem init.

Triggers ``catalog._register_all()`` on import to populate the model and
chain registries.  Importing this module is the single entry point that
ensures all models, providers, and chains are available.
"""

from . import catalog as _catalog  # noqa: F401 — registers models + chains

from .chain import ChainSpec, get_chain, list_chains
from .engine import ChainExhaustedError, ExecutionResult, execute_with_fallback
from .registry import (
    ProviderAdapter,
    ProviderConfig,
    add_provider,
    as_embedding,
    as_google,
    as_huggingface,
    as_openai,
    as_rerank,
    define_model,
    get_model,
    get_provider,
    get_providers_for_model,
    list_model_specs,
    list_models,
    list_providers,
    normalize_model_id,
    register_provider_adapter,
    resolve_model_id,
)
from .types import ModelCapability, ModelSpec
from .validation import describe_catalog, validate_catalog

__all__ = [
    "ChainExhaustedError",
    "ChainSpec",
    "ExecutionResult",
    "ModelCapability",
    "ModelSpec",
    "ProviderAdapter",
    "ProviderConfig",
    "add_provider",
    "as_embedding",
    "as_google",
    "as_huggingface",
    "as_openai",
    "as_rerank",
    "define_model",
    "execute_with_fallback",
    "get_chain",
    "get_model",
    "get_provider",
    "get_providers_for_model",
    "list_model_specs",
    "list_models",
    "list_providers",
    "list_chains",
    "normalize_model_id",
    "register_provider_adapter",
    "resolve_model_id",
    "describe_catalog",
    "validate_catalog",
]
