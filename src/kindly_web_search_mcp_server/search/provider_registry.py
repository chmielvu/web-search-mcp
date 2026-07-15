"""Provider adapters, availability selection, and diagnostics."""

from __future__ import annotations

import asyncio
import os
import threading
from enum import Enum
from types import MappingProxyType
from typing import Any, Awaitable, Mapping, Protocol, Sequence

import httpx

from ..models import WebSearchResult
from ..settings import settings
from .contracts import ContractModel, ProviderGroup
from .options import SearchOptions
from .provider_catalog import PROVIDER_DEFINITIONS_LIST, ProviderDefinition

__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFINITIONS",
    "DiagnosisCategory",
    "ProviderAdapter",
    "ProviderDefinition",
    "ProviderDiagnosis",
    "diagnose_providers",
    "get_provider_adapter",
    "get_provider_definition",
    "provider_is_reachable",
    "select_paid_google_provider",
    "select_provider_names",
]

_GOOGLE_PAID_ORDER = ("brightdata", "serper", "search_router")
_GOOGLE_RR_LOCK = threading.Lock()
_GOOGLE_RR_CURSOR = 0


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
        if query_embedding is not None and module_name == "qdrant":
            kwargs["query_embedding"] = await asyncio.shield(query_embedding)
        return await resolved_function(
            query,
            num_results=num_results,
            http_client=http_client,
            **kwargs,
        )

    return adapter


_ADAPTER_PATHS = {
    "searxng": ("searxng", "search_searxng"),
    "ddg": ("ddg", "search_ddg"),
    "gemma": ("gemma_serp", "search_gemma"),
    "degoog": ("degoog", "search_degoog"),
    "qdrant": ("qdrant", "search_qdrant"),
    "composio_llm_search": ("composio_llm_search", "search_composio_llm_search"),
    "search_router": ("search_router", "search_search_router"),
    "brave": ("brave", "search_brave"),
    "serper": ("serper", "search_serper"),
    "serpapi": ("serpapi", "search_serpapi"),
    "brightdata": ("brightdata", "search_brightdata"),
    "brightdata_bing": ("brightdata", "search_brightdata"),
    "brightdata_yandex": ("brightdata", "search_brightdata"),
    "tavily": ("tavily", "search_tavily"),
    "jina": ("jina", "search_jina"),
    "grok_openrouter": ("grok", "search_grok_openrouter"),
    "hackernews": ("hackernews", "search_hackernews"),
    "reddit": ("reddit", "search_reddit"),
    "github_graphql": ("github_graphql", "search_github_graphql"),
    "telegram": ("telegram", "search_telegram"),
    "brave_news": ("brave_news", "search_brave_news"),
}
PROVIDER_ADAPTERS: Mapping[str, ProviderAdapter] = MappingProxyType(
    {name: _make_adapter(*path) for name, path in _ADAPTER_PATHS.items()}
)
if PROVIDER_DEFINITIONS.keys() != PROVIDER_ADAPTERS.keys():
    raise RuntimeError("Provider definition and adapter keys differ")


def get_provider_definition(name: str) -> ProviderDefinition:
    return PROVIDER_DEFINITIONS[name]


def get_provider_adapter(name: str) -> ProviderAdapter:
    return PROVIDER_ADAPTERS[name]


def provider_is_reachable(definition: ProviderDefinition) -> bool:
    if not settings.providers_enabled or definition.name in settings.disabled_providers:
        return False
    if any(not os.environ.get(key, "").strip() for key in definition.all_of):
        return False
    if definition.any_of and not any(os.environ.get(key, "").strip() for key in definition.any_of):
        return False
    return True


def select_provider_names(specialized_names: Sequence[str]) -> tuple[str, ...]:
    reachable = [item for item in PROVIDER_DEFINITIONS_LIST if provider_is_reachable(item)]
    selected: list[str] = []
    seen: set[str] = set()
    for item in reachable:
        if item.group in (ProviderGroup.FREE, ProviderGroup.PAID_SERP):
            if item.name not in seen:
                selected.append(item.name)
                seen.add(item.name)
    specialized = set(specialized_names)
    for item in reachable:
        if item.group is ProviderGroup.SPECIALIZED and item.name in specialized:
            if item.name not in seen:
                selected.append(item.name)
                seen.add(item.name)
    return tuple(selected)


def select_paid_google_provider(available_names: Sequence[str]) -> str | None:
    candidates = [name for name in _GOOGLE_PAID_ORDER if name in available_names]
    if not candidates:
        return None
    global _GOOGLE_RR_CURSOR
    with _GOOGLE_RR_LOCK:
        choice = candidates[_GOOGLE_RR_CURSOR % len(candidates)]
        _GOOGLE_RR_CURSOR = (_GOOGLE_RR_CURSOR + 1) % len(candidates)
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
