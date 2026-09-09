"""Provider definitions, adapters, availability selection, and diagnostics."""

from __future__ import annotations

import asyncio
import os
import threading
from enum import Enum
from types import MappingProxyType
from typing import Any, Awaitable, Mapping, Protocol, Sequence

import httpx
from pydantic import Field

from ..models import WebSearchResult
from ..settings import settings
from .contracts import ContractModel
from .options import SearchOptions

__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFINITIONS",
    "PROVIDER_DEFINITIONS_LIST",
    "DiagnosisCategory",
    "ProviderAdapter",
    "ProviderDefinition",
    "ProviderDiagnosis",
    "brightdata_provider_call_timeout_seconds",
    "diagnose_providers",
    "get_provider_adapter",
    "get_provider_definition",
    "provider_is_reachable",
    "select_paid_google_provider",
    "select_semantic_tavily_provider",
]

_GOOGLE_PAID_ORDER = ("brightdata", "serper", "search_router")
_GOOGLE_RR_LOCK = threading.Lock()
_GOOGLE_RR_CURSOR = 0
_SEMANTIC_TAVILY_ORDER = ("tavily", "langsearch")
_SEMANTIC_TAVILY_RR_LOCK = threading.Lock()
_SEMANTIC_TAVILY_RR_CURSOR = 0


class ProviderAdapter(Protocol):
    async def __call__(
        self,
        query: str,
        *,
        num_results: int,
        options: SearchOptions,
        arguments: Mapping[str, Any],
        http_client: httpx.AsyncClient,
        query_embedding: Awaitable[Sequence[float]] | None = None,
    ) -> Sequence[WebSearchResult]: ...


class DiagnosisCategory(str, Enum):
    HEALTHY = "healthy"
    DISABLED = "disabled"
    COOLDOWN = "cooldown"
    UNCONFIGURED = "unconfigured"


class ProviderDiagnosis(ContractModel):
    name: str
    available: bool
    reason: str
    category: DiagnosisCategory = DiagnosisCategory.HEALTHY


class ProviderDefinition(ContractModel):
    name: str
    adapter_module: str
    adapter_function: str
    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    description: str
    default_timeout_seconds: float = Field(gt=0)
    requires_embedding: bool = False
    # Resilience metadata (MCP tool-design contract): how hard this provider
    # may be retried, how long to cool down after a rate limit, and any
    # per-call timeout cap below the global retrieve budget.
    per_call_timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int = Field(default=0, ge=0)
    retryable: bool = True
    cooldown_seconds: float | None = Field(default=None, ge=0)


def _definition(
    name: str,
    adapter_module: str,
    adapter_function: str,
    description: str,
    *,
    all_of: tuple[str, ...] = (),
    any_of: tuple[str, ...] = (),
    timeout: float | None = None,
    requires_embedding: bool = False,
    per_call_timeout: float | None = None,
    max_retries: int = 0,
    retryable: bool = True,
    cooldown_seconds: float | None = None,
) -> ProviderDefinition:
    return ProviderDefinition(
        name=name,
        adapter_module=adapter_module,
        adapter_function=adapter_function,
        all_of=all_of,
        any_of=any_of,
        description=description,
        default_timeout_seconds=(
            settings.search_retrieve_budget_seconds if timeout is None else timeout
        ),
        requires_embedding=requires_embedding,
        per_call_timeout_seconds=per_call_timeout,
        max_retries=max_retries,
        retryable=retryable,
        cooldown_seconds=cooldown_seconds,
    )


def brightdata_provider_call_timeout_seconds() -> float:
    return settings.search_retrieve_budget_seconds


PROVIDER_DEFINITIONS_LIST: tuple[ProviderDefinition, ...] = (
    _definition(
        "searxng",
        "providers.searxng",
        "search_searxng",
        "SearXNG metasearch",
        all_of=("SEARXNG_BASE_URL",),
        per_call_timeout=15.0,
        max_retries=1,
        cooldown_seconds=5.0,
    ),
    _definition(
        "ddg",
        "providers.ddg",
        "search_ddg",
        "DuckDuckGo search",
        max_retries=1,
        cooldown_seconds=2.0,
    ),
    _definition(
        "gemma",
        "providers.gemma_serp",
        "search_gemma",
        "Pollinations Gemini Fast search",
        all_of=("POLLINATIONS_API_KEY",),
        timeout=settings.search_retrieve_budget_seconds,
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "degoog",
        "providers.degoog",
        "search_degoog",
        "DeGoog search",
        all_of=("DEGOOG_BASE_URL",),
        max_retries=1,
        cooldown_seconds=5.0,
    ),
    _definition(
        "qdrant",
        "providers.qdrant",
        "search_qdrant",
        "Qdrant web index",
        all_of=("QDRANT_SPACE_URL",),
        requires_embedding=True,
        per_call_timeout=15.0,
        max_retries=1,
        cooldown_seconds=5.0,
    ),
    _definition(
        "composio_llm_search",
        "providers.composio_llm_search",
        "search_composio_llm_search",
        "Composio LLM search",
        all_of=("COMPOSIO_API_KEY", "COMPOSIO_USER_ID"),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "search_router",
        "providers.search_router",
        "search_search_router",
        "Search Router",
        all_of=("SEARCH_ROUTER_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "brave",
        "providers.brave",
        "search_brave",
        "Brave LLM Context",
        all_of=("BRAVE_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "serper",
        "providers.serper",
        "search_serper",
        "Serper",
        all_of=("SERPER_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "serpapi",
        "providers.serpapi",
        "search_serpapi",
        "SerpAPI",
        all_of=("SERPAPI_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "brightdata",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Google",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
        max_retries=1,
        cooldown_seconds=30.0,
    ),
    _definition(
        "brightdata_bing",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Bing",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
        max_retries=1,
        cooldown_seconds=30.0,
    ),
    _definition(
        "brightdata_yandex",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Yandex",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
        max_retries=1,
        cooldown_seconds=30.0,
    ),
    _definition(
        "tavily",
        "providers.tavily",
        "search_tavily",
        "Tavily",
        all_of=("TAVILY_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "exa",
        "providers.exa",
        "search_exa",
        "Exa semantic web search",
        all_of=("EXA_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "langsearch",
        "providers.langsearch",
        "search_langsearch",
        "LangSearch AI web search",
        all_of=("LANGSEARCH_API_KEY",),
        max_retries=1,
        cooldown_seconds=10.0,
    ),
    _definition(
        "google_discovery_engine",
        "providers.discovery_engine",
        "search_google_discovery_engine",
        "Google Discovery Engine / Agent Search",
        per_call_timeout=15.0,
        max_retries=1,
        cooldown_seconds=10.0,
    ),
)
PROVIDER_DEFINITIONS: Mapping[str, ProviderDefinition] = MappingProxyType(
    {definition.name: definition for definition in PROVIDER_DEFINITIONS_LIST}
)


def _make_adapter(module_name: str, function_name: str) -> ProviderAdapter:
    """Build an async adapter that calls the already-resolved provider function.

    The module and function are resolved eagerly at module-init time so that
    concurrent ``asyncio.wait_for`` timeouts in ``_call_provider`` are not
    consumed by synchronous ``import_module`` calls serializing on Python's
    import lock during the six-branch fan-out.
    """
    from importlib import import_module

    from .provider_call import build_provider_call_kwargs

    resolved_module = import_module(f"{__package__}.{module_name}")
    resolved_function = getattr(resolved_module, function_name)

    async def adapter(
        query: str,
        *,
        num_results: int,
        options: SearchOptions,
        arguments: Mapping[str, Any],
        http_client: httpx.AsyncClient,
        query_embedding: Awaitable[Sequence[float]] | None = None,
    ) -> Sequence[WebSearchResult]:
        kwargs = build_provider_call_kwargs(
            resolved_function,
            search_options=options,
            provider_arguments=arguments,
        )
        if query_embedding is not None and module_name == "providers.qdrant":
            kwargs["query_embedding"] = await asyncio.shield(query_embedding)
        return await resolved_function(
            query,
            num_results=num_results,
            http_client=http_client,
            **kwargs,
        )

    return adapter


PROVIDER_ADAPTERS: Mapping[str, ProviderAdapter] = MappingProxyType(
    {
        definition.name: _make_adapter(definition.adapter_module, definition.adapter_function)
        for definition in PROVIDER_DEFINITIONS_LIST
    }
)
if set(PROVIDER_ADAPTERS) != set(PROVIDER_DEFINITIONS):
    raise RuntimeError("Provider definition and adapter keys differ")


def get_provider_definition(name: str) -> ProviderDefinition:
    return PROVIDER_DEFINITIONS[name]


def get_provider_adapter(name: str) -> ProviderAdapter:
    return PROVIDER_ADAPTERS[name]


def provider_is_reachable(definition: ProviderDefinition) -> bool:
    if not settings.providers_enabled or definition.name in settings.disabled_providers:
        return False
    if definition.name == "serpapi" and not settings.serpapi_enabled:
        return False
    if any(not os.environ.get(key, "").strip() for key in definition.all_of):
        return False
    if definition.any_of and not any(os.environ.get(key, "").strip() for key in definition.any_of):
        return False
    if definition.name == "google_discovery_engine":
        from .providers.discovery_engine import credentials_available

        if not credentials_available():
            return False
    return True


def select_provider_names() -> tuple[str, ...]:
    """All reachable providers; branch candidate tuples decide routing."""
    return tuple(item.name for item in PROVIDER_DEFINITIONS_LIST if provider_is_reachable(item))


def select_paid_google_provider(available_names: Sequence[str]) -> str | None:
    candidates = [name for name in _GOOGLE_PAID_ORDER if name in available_names]
    if not candidates:
        return None
    global _GOOGLE_RR_CURSOR
    with _GOOGLE_RR_LOCK:
        choice = candidates[_GOOGLE_RR_CURSOR % len(candidates)]
        _GOOGLE_RR_CURSOR = (_GOOGLE_RR_CURSOR + 1) % len(candidates)
    return choice


def select_semantic_tavily_provider(available_names: Sequence[str]) -> str | None:
    candidates = [name for name in _SEMANTIC_TAVILY_ORDER if name in available_names]
    if not candidates:
        return None
    global _SEMANTIC_TAVILY_RR_CURSOR
    with _SEMANTIC_TAVILY_RR_LOCK:
        choice = candidates[_SEMANTIC_TAVILY_RR_CURSOR % len(candidates)]
        _SEMANTIC_TAVILY_RR_CURSOR = (_SEMANTIC_TAVILY_RR_CURSOR + 1) % len(candidates)
    return choice


def diagnose_providers() -> list[ProviderDiagnosis]:
    diagnoses: list[ProviderDiagnosis] = []
    for definition in PROVIDER_DEFINITIONS_LIST:
        if not settings.providers_enabled or definition.name in settings.disabled_providers:
            diagnoses.append(
                ProviderDiagnosis(
                    name=definition.name,
                    available=False,
                    reason="disabled",
                    category=DiagnosisCategory.DISABLED,
                )
            )
        elif not provider_is_reachable(definition):
            diagnoses.append(
                ProviderDiagnosis(
                    name=definition.name,
                    available=False,
                    reason="missing credentials",
                    category=DiagnosisCategory.UNCONFIGURED,
                )
            )
        else:
            diagnoses.append(ProviderDiagnosis(name=definition.name, available=True, reason="ok"))
    return diagnoses
