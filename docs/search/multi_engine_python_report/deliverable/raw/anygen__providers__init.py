"""Provider registry."""
from __future__ import annotations

from hsearch.providers.base import SearchProvider, ProviderAuthError, ProviderHTTPError
from hsearch.providers.brave import BraveProvider
from hsearch.providers.serper import SerperProvider
from hsearch.providers.exa import ExaProvider
from hsearch.providers.tavily import TavilyProvider
from hsearch.providers.firecrawl import FirecrawlProvider
from hsearch.providers.jina import JinaProvider

_REGISTRY: dict[str, type[SearchProvider]] = {
    "brave": BraveProvider,
    "serper": SerperProvider,
    "exa": ExaProvider,
    "tavily": TavilyProvider,
    "firecrawl": FirecrawlProvider,
    "jina": JinaProvider,
}


def get_provider(name: str) -> SearchProvider:
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise KeyError(f"Unknown provider: {name}")
    return cls()


def list_providers() -> list[str]:
    return list(_REGISTRY)


__all__ = [
    "SearchProvider",
    "ProviderAuthError",
    "ProviderHTTPError",
    "get_provider",
    "list_providers",
]
