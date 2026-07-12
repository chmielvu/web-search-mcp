"""Immutable provider metadata and callable registry."""

from __future__ import annotations

import os
from enum import Enum
import threading
from types import MappingProxyType
from typing import Any, Awaitable, Mapping, Protocol, Sequence

import httpx
from pydantic import Field

from ..models import WebSearchResult
from ..settings import settings
from .contracts import ContractModel, ProviderGroup, ProviderTarget
from .options import SearchOptions


class ProviderDefinition(ContractModel):
    name: str
    group: ProviderGroup
    targets: frozenset[ProviderTarget]
    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    description: str
    default_timeout_seconds: float = Field(gt=0)
    supported_options: frozenset[str] = frozenset()
    requires_embedding: bool = False
    search_engine: str | None = None


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


def _definition(
    name: str,
    group: ProviderGroup,
    targets: tuple[ProviderTarget, ...],
    description: str,
    *,
    all_of: tuple[str, ...] = (),
    timeout: float = 10.0,
    requires_embedding: bool = False,
) -> ProviderDefinition:
    return ProviderDefinition(
        name=name,
        group=group,
        targets=frozenset(targets),
        all_of=all_of,
        description=description,
        default_timeout_seconds=timeout,
        requires_embedding=requires_embedding,
    )


_DEFINITIONS = (
    _definition(
        "searxng",
        ProviderGroup.FREE,
        (ProviderTarget.ORIGINAL, ProviderTarget.KEYWORD),
        "SearXNG metasearch",
        all_of=("SEARXNG_BASE_URL",),
    ),
    _definition(
        "ddg",
        ProviderGroup.FREE,
        (ProviderTarget.ORIGINAL, ProviderTarget.KEYWORD),
        "DuckDuckGo search",
    ),
    _definition(
        "gemma",
        ProviderGroup.FREE,
        (ProviderTarget.ORIGINAL, ProviderTarget.NEURAL),
        "Gemini grounded search",
    ),
    _definition(
        "degoog",
        ProviderGroup.FREE,
        (ProviderTarget.ORIGINAL, ProviderTarget.KEYWORD),
        "DeGoog search",
        all_of=("DEGOOG_BASE_URL",),
    ),
    _definition(
        "qdrant",
        ProviderGroup.FREE,
        (ProviderTarget.NEURAL,),
        "Qdrant web index",
        all_of=("QDRANT_SPACE_URL",),
        requires_embedding=True,
    ),
    _definition(
        "composio_llm_search",
        ProviderGroup.FREE,
        (ProviderTarget.NEURAL,),
        "Composio LLM search",
        all_of=("COMPOSIO_API_KEY", "COMPOSIO_USER_ID"),
    ),
    _definition(
        "search_router",
        ProviderGroup.PAID_SERP,
        (ProviderTarget.KEYWORD,),
        "Search Router",
        all_of=("SEARCH_ROUTER_API_KEY",),
    ),
    _definition(
        "brave",
        ProviderGroup.PAID_SERP,
        (ProviderTarget.KEYWORD,),
        "Brave LLM Context",
        all_of=("BRAVE_API_KEY",),
    ),
    _definition(
        "serper",
        ProviderGroup.PAID_SERP,
        (ProviderTarget.KEYWORD,),
        "Serper",
        all_of=("SERPER_API_KEY",),
    ),
    _definition(
        "serpapi",
        ProviderGroup.PAID_SERP,
        (ProviderTarget.KEYWORD,),
        "SerpAPI",
        all_of=("SERPAPI_API_KEY",),
    ),
    _definition(
        "brightdata",
        ProviderGroup.PAID_SERP,
        (ProviderTarget.KEYWORD,),
        "Bright Data",
        all_of=("BRIGHTDATA_API_KEY",),
    ),
    _definition(
        "tavily",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.NEURAL,),
        "Tavily",
        all_of=("TAVILY_API_KEY",),
    ),
    _definition(
        "jina",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.NEURAL,),
        "Jina search",
        all_of=("JINA_API_KEY",),
    ),
    _definition(
        "grok_openrouter",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.NEURAL,),
        "Grok via OpenRouter",
        all_of=("OPENROUTER_API_KEY",),
    ),
    _definition(
        "hackernews", ProviderGroup.SPECIALIZED, (ProviderTarget.COMMUNITY,), "Hacker News"
    ),
    _definition("reddit", ProviderGroup.SPECIALIZED, (ProviderTarget.COMMUNITY,), "Reddit"),
    _definition(
        "github_graphql",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.COMMUNITY,),
        "GitHub GraphQL",
        all_of=("GITHUB_TOKEN",),
    ),
    _definition(
        "telegram",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.COMMUNITY,),
        "Telegram",
        all_of=("TELEGRAM_API_ID", "TELEGRAM_API_HASH"),
    ),
    _definition(
        "brave_news",
        ProviderGroup.SPECIALIZED,
        (ProviderTarget.COMMUNITY,),
        "Brave News",
        all_of=("BRAVE_API_KEY",),
    ),
)

PROVIDER_DEFINITIONS: Mapping[str, ProviderDefinition] = MappingProxyType(
    {definition.name: definition for definition in _DEFINITIONS}
)


def _lazy_adapter(module_name: str, function_name: str) -> ProviderAdapter:
    async def adapter(
        query: str,
        *,
        num_results: int,
        options: SearchOptions,
        arguments: Mapping[str, Any],
        http_client: httpx.AsyncClient,
        query_embedding: Awaitable[Sequence[float]] | None = None,
    ) -> Sequence[WebSearchResult]:
        from importlib import import_module

        from .provider_call import build_provider_call_kwargs

        function = getattr(import_module(f"{__package__}.{module_name}"), function_name)
        kwargs = build_provider_call_kwargs(
            function,
            search_options=options,
            provider_arguments=arguments,
        )
        if query_embedding is not None and module_name == "qdrant":
            kwargs["query_embedding"] = await query_embedding
        return await function(
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
    {name: _lazy_adapter(*path) for name, path in _ADAPTER_PATHS.items()}
)
if PROVIDER_DEFINITIONS.keys() != PROVIDER_ADAPTERS.keys():
    raise RuntimeError("Provider definition and adapter keys differ")

_PAID_RR_LOCK = threading.Lock()
_PAID_RR_CURSOR = 0


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
    reachable = [item for item in _DEFINITIONS if provider_is_reachable(item)]
    selected = [item.name for item in reachable if item.group is ProviderGroup.FREE]
    paid = [item for item in reachable if item.group is ProviderGroup.PAID_SERP]
    brightdata = next((item for item in paid if item.name == "brightdata"), None)
    others = [item for item in paid if item.name != "brightdata"]
    if brightdata is not None:
        selected.append(brightdata.name)
    if others:
        global _PAID_RR_CURSOR
        with _PAID_RR_LOCK:
            selected.append(others[_PAID_RR_CURSOR % len(others)].name)
            _PAID_RR_CURSOR = (_PAID_RR_CURSOR + 1) % len(others)
    specialized = set(specialized_names)
    selected.extend(
        item.name
        for item in reachable
        if item.group is ProviderGroup.SPECIALIZED and item.name in specialized
    )
    return tuple(selected)


def diagnose_providers() -> list[ProviderDiagnosis]:
    diagnoses: list[ProviderDiagnosis] = []
    for definition in _DEFINITIONS:
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
