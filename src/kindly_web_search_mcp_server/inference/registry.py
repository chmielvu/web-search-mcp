"""
Unified Model & Provider Registry
====================================

Single source of truth for all models and providers consumed by the inference
engine.  Every model is defined *once* as a canonical entry, then associated
with one or more providers that can serve it.  This eliminates the old pattern
of defining the same model N times across N chain builders.

-------------------------------------------------------------------------------
HOW TO ADD A MODEL
-------------------------------------------------------------------------------

1. Add a ``define_model(...)`` call with the canonical ID, a human-readable
   name, a short description, and the capabilities it supports.

2. For each provider that can serve the model, add a
   ``add_provider(canonical_id, "provider_name", ProviderConfig(...))`` call.

   Use the ``as_openai()``, ``as_google()``, ``as_huggingface()``,
   ``as_rerank()``, or ``as_embedding()`` helper functions to create the
   ``ProviderConfig`` — these set sensible defaults for each provider family.

3. (One-time) Ensure a provider adapter is registered via
   ``register_provider_adapter()``.  Adapters self-register on import; see
   ``adapters/__init__.py``.

-------------------------------------------------------------------------------
HOW TO ADD A PROVIDER TO AN EXISTING MODEL
-------------------------------------------------------------------------------

Add a single ``add_provider(canonical_id, "new_provider", ProviderConfig(...))``
call.  The model's capabilities are inherited automatically — no need to repeat
them.

-------------------------------------------------------------------------------
CHAIN REFERENCE FORMAT
-------------------------------------------------------------------------------

Chains reference models as ``"canonical_id@provider"``.  The registry splits
on ``@`` to resolve the canonical model and the provider.

Example: ``"gpt-oss-120b@cerebras"`` → canonical model ``gpt-oss-120b``
                                      served by ``cerebras``.

-------------------------------------------------------------------------------
MODEL ID NORMALIZATION
-------------------------------------------------------------------------------

Different providers often expose the same underlying model under different
names.  For example, the same Llama-derived model is:

  - ``"gpt-oss-120b"``       on Cerebras
  - ``"openai/gpt-oss-120b"`` on Groq
  - ``"openai/gpt-oss-120b:nscale"`` on HuggingFace / Nscale

The ``ProviderConfig.model_id`` field captures each provider's local name.
The ``resolve_model_id(canonical_id, provider)`` helper returns the
provider-specific string, and ``normalize_model_id(model_id)`` strips
provider prefixes to get the canonical form.

-------------------------------------------------------------------------------
PROVIDER QUIRKS
-------------------------------------------------------------------------------

- **Cerebras / Groq / Vercel**: OpenAI-compatible.  Use ``as_openai()``.
- **HuggingFace**: Uses ``huggingface_hub.InferenceClient`` (sync, runs in
  thread).  Use ``as_huggingface()``.
- **Google / Gemini**: Native GenAI SDK.  Use ``as_google()``.
- **Cohere / Voyage / OpenRouter (rerank)**: HTTP-based rerank.  Use
  ``as_rerank()``.
- **Embedding**: HuggingFace feature extraction.  Use ``as_embedding()``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Awaitable, Callable

from .types import LLMGeneration, ModelCapability, ModelSpec

logger = logging.getLogger(__name__)

# ============================================================================
# Public types
# ============================================================================


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """
    Per-provider delivery details for a canonical model.

    Fields:
        model_id:        The model name/ID as recognised by this provider.
        base_url:        API base URL (``None`` for providers that use
                         their own default, e.g. Google GenAI).
        api_key_env:     Environment variable name that holds the API key.
        cost_per_1m_input:   USD per 1M input tokens (``None`` if unknown).
        cost_per_1m_output:  USD per 1M output tokens (``None`` if unknown).
        default_timeout:     Default per-call timeout in seconds.
    """

    model_id: str
    base_url: str | None
    api_key_env: str
    cost_per_1m_input: float | None = None
    cost_per_1m_output: float | None = None
    default_timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    """
    Canonical definition of one logical model.

    Fields:
        canonical_id:   Stable identifier used in chain references
                        (e.g. ``"gpt-oss-120b"``).
        display_name:   Human-readable name (e.g. ``"Llama 3.3 120B"``).
        description:    Short description of what this model is / does.
        capabilities:   Set of ``ModelCapability`` values this model supports.
        provider_configs:  Map of ``provider_name -> ProviderConfig``.
    """

    canonical_id: str
    display_name: str
    description: str
    capabilities: frozenset[ModelCapability]
    provider_configs: dict[str, ProviderConfig]


@dataclass(frozen=True, slots=True)
class ProviderAdapter:
    """
    Registered provider adapter — a callable that knows how to call one
    provider family's API.

    Fields:
        name:          Provider name (e.g. ``"cerebras"``, ``"google"``).
        execute:       Async function ``(ModelSpec, **kwargs) -> LLMGeneration``.
        capabilities:  Capabilities this adapter can fulfil.
    """

    name: str
    execute: Callable[..., Awaitable[LLMGeneration]]
    capabilities: frozenset[ModelCapability]


# ============================================================================
# ProviderConfig helpers — these set sensible defaults for each provider family
# ============================================================================


def as_openai(
    model_id: str,
    *,
    api_key_env: str,
    base_url: str | None = None,
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
) -> ProviderConfig:
    """
    Build a ``ProviderConfig`` for an OpenAI-compatible provider.

    Args:
        model_id:  Model name as the provider knows it.
        api_key_env:  Env var holding the API key.
        base_url:  API base URL.  If ``None``, the adapter uses the
                   provider's default base URL (e.g. Vercel AI Gateway).
        cost_per_1m_input:  USD per 1M input tokens.
        cost_per_1m_output: USD per 1M output tokens.
        default_timeout:  Default timeout in seconds.
    """
    return ProviderConfig(
        model_id=model_id,
        base_url=base_url,
        api_key_env=api_key_env,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        default_timeout=default_timeout,
    )


def as_google(
    model_id: str,
    *,
    api_key_env: str = "GEMINI_API_KEY",
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
) -> ProviderConfig:
    """
    Build a ``ProviderConfig`` for Google GenAI.

    Google uses its own SDK (``google.genai``) and does not require a
    ``base_url`` — the SDK handles endpoint selection.
    """
    return ProviderConfig(
        model_id=model_id,
        base_url=None,
        api_key_env=api_key_env,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        default_timeout=default_timeout,
    )


def as_huggingface(
    model_id: str,
    *,
    api_key_env: str = "HF_TOKEN",
    base_url: str = "https://router.huggingface.co",
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
) -> ProviderConfig:
    """
    Build a ``ProviderConfig`` for HuggingFace InferenceClient.

    The adapter uses ``huggingface_hub.InferenceClient`` (synchronous,
    dispatched in a thread).  The ``base_url`` defaults to the HF router.
    """
    return ProviderConfig(
        model_id=model_id,
        base_url=base_url,
        api_key_env=api_key_env,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        default_timeout=default_timeout,
    )


def as_rerank(
    model_id: str,
    *,
    api_key_env: str,
    base_url: str,
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
) -> ProviderConfig:
    """
    Build a ``ProviderConfig`` for a rerank endpoint (Cohere, Voyage, etc.).

    Rerank providers are HTTP-based (not OpenAI-compatible) and use a
    different request/response format.
    """
    return ProviderConfig(
        model_id=model_id,
        base_url=base_url,
        api_key_env=api_key_env,
        cost_per_1m_input=cost_per_1m_input,
        cost_per_1m_output=cost_per_1m_output,
        default_timeout=default_timeout,
    )


def as_embedding(
    model_id: str,
    *,
    api_key_env: str = "HF_TOKEN",
    base_url: str = "https://api-inference.huggingface.co",
    default_timeout: float = 30.0,
) -> ProviderConfig:
    """
    Build a ``ProviderConfig`` for a HuggingFace embedding endpoint.

    Uses the same ``InferenceClient`` but with the ``feature_extraction``
    pipeline instead of chat completion.
    """
    return ProviderConfig(
        model_id=model_id,
        base_url=base_url,
        api_key_env=api_key_env,
        default_timeout=default_timeout,
    )


# ============================================================================
# Multi-key registration helpers
# ============================================================================

_SLOT_SUFFIXES = ("", ":second", ":third", ":fourth", ":fifth")


def _provider_key_for_slot(provider: str, index: int) -> str:
    if index < 0:
        raise ValueError("provider slot index must be >= 0")
    if index >= len(_SLOT_SUFFIXES):
        raise ValueError(
            f"Too many API keys for provider '{provider}': "
            f"supported slots are {list(_SLOT_SUFFIXES)}"
        )
    return f"{provider}{_SLOT_SUFFIXES[index]}"


def _normalize_api_key_envs(api_key_envs: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(api_key_envs, str):
        values = [api_key_envs]
    else:
        values = list(api_key_envs)
    normalized = [value.strip() for value in values if value and value.strip()]
    if not normalized:
        raise ValueError("At least one api_key_env is required")
    return normalized


def add_openai_providers(
    canonical_id: str,
    provider: str,
    *,
    model_id: str,
    api_key_envs: str | list[str] | tuple[str, ...],
    base_url: str | None = None,
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
) -> list[str]:
    """Register one OpenAI-compatible provider with one or more API-key slots.

    Returns the ordered provider keys that were registered, e.g.::

        ["groq", "groq:second"]
    """
    keys = _normalize_api_key_envs(api_key_envs)
    provider_keys: list[str] = []
    for index, api_key_env in enumerate(keys):
        provider_key = _provider_key_for_slot(provider, index)
        add_provider(
            canonical_id,
            provider_key,
            as_openai(
                model_id=model_id,
                api_key_env=api_key_env,
                base_url=base_url,
                cost_per_1m_input=cost_per_1m_input,
                cost_per_1m_output=cost_per_1m_output,
                default_timeout=default_timeout,
            ),
        )
        provider_keys.append(provider_key)
    return provider_keys


def add_google_providers(
    canonical_id: str,
    provider: str = "google",
    *,
    model_id: str,
    api_key_envs: str | list[str] | tuple[str, ...],
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
    default_timeout: float = 30.0,
    provider_keys: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Register one Google provider with one or more API-key/timeout slots.

    When ``provider_keys`` is omitted, slots follow the standard
    ``provider``, ``provider:second``, ... convention.  Explicit keys are
    useful for timeout variants such as ``google:rankllm``.
    """
    keys = _normalize_api_key_envs(api_key_envs)
    if provider_keys is None:
        resolved_keys = [_provider_key_for_slot(provider, index) for index in range(len(keys))]
    else:
        resolved_keys = list(provider_keys)
        if len(resolved_keys) != len(keys):
            raise ValueError(
                "provider_keys length must match api_key_envs "
                f"({len(resolved_keys)} != {len(keys)})"
            )

    for provider_key, api_key_env in zip(resolved_keys, keys, strict=True):
        add_provider(
            canonical_id,
            provider_key,
            as_google(
                model_id=model_id,
                api_key_env=api_key_env,
                cost_per_1m_input=cost_per_1m_input,
                cost_per_1m_output=cost_per_1m_output,
                default_timeout=default_timeout,
            ),
        )
    return resolved_keys


def chain_refs(canonical_id: str, provider_keys: list[str] | tuple[str, ...]) -> list[str]:
    """Build ordered ``canonical_id@provider_key`` chain references."""
    return [f"{canonical_id}@{provider_key}" for provider_key in provider_keys]


# ============================================================================
# Model ID normalisation
# ============================================================================

# Common provider prefixes stripped during normalisation.
_PROVIDER_PREFIXES = (
    "openai/",
    "google/",
    "cohere/",
    "voyage/",
    "groq/",
)


def normalize_model_id(model_id: str) -> str:
    """
    Strip known provider prefixes from a model ID to get the canonical form.

    Examples::
        ``"openai/gpt-oss-120b"``  → ``"gpt-oss-120b"``
        ``"google/gemini-3.1-flash-lite"`` → ``"gemini-3.1-flash-lite"``
        ``"cohere/rerank-v4.0-fast"`` → ``"rerank-v4.0-fast"``
    """
    for prefix in _PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def resolve_model_id(canonical_id: str, provider: str) -> str:
    """
    Return the provider-specific model string for a canonical model.

    Raises ``KeyError`` if the canonical model is not registered or the
    provider is not configured for it.
    """
    model_def = _get_model_def(canonical_id)
    pcfg = _get_provider_config(model_def, provider)
    return pcfg.model_id


def _spec_id(canonical_id: str, provider_key: str) -> str:
    """Build a chain-reference spec ID from canonical ID and provider key."""
    return f"{canonical_id}@{provider_key}"


def _parse_spec_id(spec_id: str) -> tuple[str, str]:
    """Split a spec ID into ``(canonical_id, provider_key)``."""
    if "@" not in spec_id:
        raise ValueError(f"Invalid spec_id '{spec_id}': expected format 'canonical_id@provider'")
    parts = spec_id.split("@", 1)
    return parts[0], parts[1]


def _resolve_adapter_name(provider_key: str) -> str:
    """Resolve a qualified provider key to the base adapter name.

    Qualified keys use ``:`` to separate the adapter name from a
    configuration suffix (e.g. ``"google:second"`` → ``"google"``).
    The suffix selects which ``ProviderConfig`` to use for a model
    while reusing the same adapter callable.
    """
    return provider_key.split(":")[0]


# ============================================================================
# Internal storage
# ============================================================================

_MODEL_DEFINITIONS: dict[str, ModelDefinition] = {}
_PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {}
_LOCK = threading.RLock()
_ADAPTERS_LOADED = False


def _ensure_provider_adapters() -> None:
    """Load adapters on first registry lookup, avoiding telemetry import cycles."""
    global _ADAPTERS_LOADED
    if _ADAPTERS_LOADED:
        return
    with _LOCK:
        if _ADAPTERS_LOADED:
            return
        from . import adapters as _adapters  # noqa: F401 — self-registration

        _ADAPTERS_LOADED = True


def _get_model_def(canonical_id: str) -> ModelDefinition:
    with _LOCK:
        if canonical_id not in _MODEL_DEFINITIONS:
            raise KeyError(f"Unknown model: {canonical_id}")
        return _MODEL_DEFINITIONS[canonical_id]


def _get_provider_config(model_def: ModelDefinition, provider: str) -> ProviderConfig:
    if provider not in model_def.provider_configs:
        raise KeyError(
            f"Provider '{provider}' is not configured for model "
            f"'{model_def.canonical_id}'. "
            f"Available: {list(model_def.provider_configs.keys())}"
        )
    return model_def.provider_configs[provider]


# ============================================================================
# Public registration API
# ============================================================================


def define_model(
    canonical_id: str,
    *,
    display_name: str,
    description: str = "",
    capabilities: set[ModelCapability] | None = None,
) -> ModelDefinition:
    """
    Register a canonical model.

    Args:
        canonical_id:  Stable identifier (e.g. ``"gpt-oss-120b"``).
        display_name:  Human-readable name (e.g. ``"Llama 3.3 120B"``).
        description:   Short description of what this model is / does.
        capabilities:  Set of ``ModelCapability`` values.

    Returns:
        The newly created ``ModelDefinition``.

    Example::

        define_model(
            "gpt-oss-120b",
            display_name="Llama 3.3 120B",
            description="Primary worker LLM — OpenAI-compatible, fast, cheap.",
            capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
        )
    """
    caps = frozenset(capabilities or {ModelCapability.CHAT})
    with _LOCK:
        if canonical_id in _MODEL_DEFINITIONS:
            logger.warning("Overwriting existing model definition: %s", canonical_id)
        model_def = ModelDefinition(
            canonical_id=canonical_id,
            display_name=display_name,
            description=description,
            capabilities=caps,
            provider_configs={},
        )
        _MODEL_DEFINITIONS[canonical_id] = model_def
        return model_def


def add_provider(
    canonical_id: str,
    provider: str,
    config: ProviderConfig,
) -> None:
    """
    Associate a provider with a canonical model.

    Args:
        canonical_id:  The model's canonical ID.
        provider:      Provider name (e.g. ``"cerebras"``, ``"groq"``).
        config:        ``ProviderConfig`` with delivery details.

    Example::

        add_provider(
            "gpt-oss-120b",
            "cerebras",
            as_openai(
                model_id="gpt-oss-120b",
                api_key_env="CEREBRAS_API_KEY",
                base_url="https://api.cerebras.ai/v1",
                cost_per_1m_input=0.35,
                cost_per_1m_output=0.75,
            ),
        )
    """
    with _LOCK:
        model_def = _get_model_def(canonical_id)
        if provider in model_def.provider_configs:
            logger.warning(
                "Overwriting provider '%s' config for model '%s'",
                provider,
                canonical_id,
            )
        # Rebuild the frozen dataclass with the new provider_configs dict
        updated_configs = dict(model_def.provider_configs)
        updated_configs[provider] = config
        _MODEL_DEFINITIONS[canonical_id] = ModelDefinition(
            canonical_id=model_def.canonical_id,
            display_name=model_def.display_name,
            description=model_def.description,
            capabilities=model_def.capabilities,
            provider_configs=updated_configs,
        )


def register_provider_adapter(adapter: ProviderAdapter) -> None:
    """
    Register a provider adapter (callable that knows how to call one API).

    Adapters typically self-register when their module is imported.
    """
    if adapter.name in _PROVIDER_ADAPTERS:
        logger.warning("Overwriting existing provider adapter: %s", adapter.name)
    _PROVIDER_ADAPTERS[adapter.name] = adapter


def register_provider_alias(alias: str, target: str) -> None:
    """
    Register an alias from one provider name to another.

    This is useful when multiple providers share the same adapter (e.g.
    ``cerebras``, ``groq``, ``vercel`` all use the OpenAI-compatible adapter).

    The alias must be registered *after* the target adapter.
    """
    if target not in _PROVIDER_ADAPTERS:
        raise KeyError(f"Cannot alias '{alias}' to unknown provider '{target}'")
    _PROVIDER_ADAPTERS[alias] = _PROVIDER_ADAPTERS[target]
    logger.debug("Registered provider alias '%s' -> '%s'", alias, target)


# ============================================================================
# Public query API
# ============================================================================


def get_model(spec_id: str) -> ModelSpec:
    """
    Resolve a chain reference (``"canonical_id@provider_key"``) to a ``ModelSpec``.

    The spec is generated on-the-fly from the canonical model definition
    and the provider's configuration.  The ``ModelSpec.provider`` field is
    the *base* adapter name (e.g. ``"google"``), not the qualified key
    (e.g. ``"google:second"``).
    """
    canonical_id, provider_key = _parse_spec_id(spec_id)
    adapter_name = _resolve_adapter_name(provider_key)
    with _LOCK:
        model_def = _get_model_def(canonical_id)
        pcfg = _get_provider_config(model_def, provider_key)
        return ModelSpec(
            spec_id=spec_id,
            provider=adapter_name,
            model_id=pcfg.model_id,
            base_url=pcfg.base_url,
            api_key_env=pcfg.api_key_env,
            capabilities=model_def.capabilities,
            cost_per_1m_input=pcfg.cost_per_1m_input,
            cost_per_1m_output=pcfg.cost_per_1m_output,
            default_timeout=pcfg.default_timeout,
            source="catalog",
        )


def get_provider(name: str) -> ProviderAdapter:
    """Look up a registered provider adapter by name.

    Qualified keys like ``"google:second"`` resolve to the base adapter
    ``"google"`` automatically.
    """
    _ensure_provider_adapters()
    base = _resolve_adapter_name(name)
    if base not in _PROVIDER_ADAPTERS:
        raise KeyError(
            f"Unknown provider: '{name}' (resolved to '{base}'). "
            f"Available: {list(_PROVIDER_ADAPTERS.keys())}"
        )
    return _PROVIDER_ADAPTERS[base]


def list_models(*, capability: ModelCapability | None = None) -> list[ModelDefinition]:
    """List all registered model definitions, optionally filtered by capability."""
    with _LOCK:
        models = list(_MODEL_DEFINITIONS.values())
    if capability is not None:
        return [m for m in models if capability in m.capabilities]
    return models


def list_model_specs(*, capability: ModelCapability | None = None) -> list[ModelSpec]:
    """
    List all registered model specs (one per model/provider combination),
    optionally filtered by capability.
    """
    result: list[ModelSpec] = []
    with _LOCK:
        for model_def in _MODEL_DEFINITIONS.values():
            if capability is not None and capability not in model_def.capabilities:
                continue
            for provider_key in model_def.provider_configs:
                pcfg = model_def.provider_configs[provider_key]
                result.append(
                    ModelSpec(
                        spec_id=_spec_id(model_def.canonical_id, provider_key),
                        provider=_resolve_adapter_name(provider_key),
                        model_id=pcfg.model_id,
                        base_url=pcfg.base_url,
                        api_key_env=pcfg.api_key_env,
                        capabilities=model_def.capabilities,
                        cost_per_1m_input=pcfg.cost_per_1m_input,
                        cost_per_1m_output=pcfg.cost_per_1m_output,
                        default_timeout=pcfg.default_timeout,
                        source="catalog",
                    )
                )
    return result


def list_providers() -> list[str]:
    """List all registered provider adapter names."""
    _ensure_provider_adapters()
    return list(_PROVIDER_ADAPTERS.keys())


def get_providers_for_model(canonical_id: str) -> list[str]:
    """List all provider keys configured for a given canonical model."""
    model_def = _get_model_def(canonical_id)
    return list(model_def.provider_configs.keys())
