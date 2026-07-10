"""Search providers: SearXNG (primary) + DDG (peer free) → intent-owned providers.

Uses Reciprocal Rank Fusion (RRF) for multi-provider result merging.
Includes circuit breaker and budget tracking for provider health.

Provider selection is driven by the resolved search plan and explicit allow-lists.
"""

from __future__ import annotations

import logging
from .brave import search_brave
from .brave_news import search_brave_news
from .brightdata import search_brightdata
from .serpapi import search_serpapi
from .serper import search_serper
from .composio_llm_search import search_composio_llm_search
from .ddg import search_ddg
from .degoog import search_degoog
from .gemma_serp import search_gemma
from .grok import search_grok_openrouter
from .github_graphql import search_github_graphql
from .hackernews import search_hackernews
from .jina import search_jina
from .qdrant import search_qdrant
from .reddit import search_reddit
from .telegram import search_telegram
from .provider_config import (
    ProviderConfig,
    ProviderGroup,
    register_provider,
)
from .searxng import search_searxng
from .search_router import search_search_router
from .tavily import search_tavily
from .budget import ProviderBudget

from .errors import WebSearchProviderError
from .provider_execution import _search_single_provider

LOGGER = logging.getLogger(__name__)

__all__ = [
    "ProviderBudget",
    "ProviderConfig",
    "WebSearchProviderError",
    "_search_single_provider",
]


def _init_provider_registry() -> None:
    """Initialize provider registry with configured modes."""
    # Tier 1: Free providers (always fire concurrently)
    register_provider(
        ProviderConfig(
            name="searxng",
            env_key="SEARXNG_BASE_URL",
            search_fn=search_searxng,
            group=ProviderGroup.free,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="ddg",
            env_key="",  # No env key needed
            search_fn=search_ddg,
            group=ProviderGroup.free,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="gemma",
            env_key="",  # Uses baked-in API key
            search_fn=search_gemma,
            group=ProviderGroup.free,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="degoog",
            env_key="DEGOOG_BASE_URL",
            search_fn=search_degoog,
            group=ProviderGroup.free,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="search_router",
            env_key="SEARCH_ROUTER_API_KEY",
            search_fn=search_search_router,
            group=ProviderGroup.paid_serp,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="qdrant",
            env_key="QDRANT_SPACE_URL",
            search_fn=search_qdrant,
            group=ProviderGroup.free,
            requires_key=False,
        )
    )

    # Tier 2: Paid SERP providers (always fire concurrently)
    register_provider(
        ProviderConfig(
            name="tavily",
            env_key="TAVILY_API_KEY",
            search_fn=search_tavily,
            group=ProviderGroup.specialized,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="brave",
            env_key="BRAVE_API_KEY",
            search_fn=search_brave,
            group=ProviderGroup.paid_serp,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="serper",
            env_key="SERPER_API_KEY",
            search_fn=search_serper,
            group=ProviderGroup.paid_serp,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="serpapi",
            env_key="SERPAPI_API_KEY",
            search_fn=search_serpapi,
            group=ProviderGroup.paid_serp,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="brightdata",
            env_key="BRIGHTDATA_API_KEY",
            search_fn=search_brightdata,
            group=ProviderGroup.paid_serp,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="jina",
            env_key="JINA_API_KEY",
            search_fn=search_jina,
            group=ProviderGroup.specialized,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="grok_openrouter",
            env_key="OPENROUTER_API_KEY",
            search_fn=search_grok_openrouter,
            group=ProviderGroup.specialized,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="composio_llm_search",
            env_key="COMPOSIO_API_KEY",
            search_fn=search_composio_llm_search,
            group=ProviderGroup.free,
            requires_key=True,
            extra_env_keys=("COMPOSIO_USER_ID",),
        )
    )

    # Tier 3: Specialized providers (fire when named in intent policy)
    register_provider(
        ProviderConfig(
            name="hackernews",
            env_key="",
            search_fn=search_hackernews,
            group=ProviderGroup.specialized,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="reddit",
            env_key="",
            search_fn=search_reddit,
            group=ProviderGroup.specialized,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="github_graphql",
            env_key="GITHUB_TOKEN",
            search_fn=search_github_graphql,
            group=ProviderGroup.specialized,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="telegram",
            env_key="TELEGRAM_API_ID",
            search_fn=search_telegram,
            group=ProviderGroup.specialized,
            requires_key=True,
            extra_env_keys=("TELEGRAM_API_HASH",),
        )
    )

    register_provider(
        ProviderConfig(
            name="brave_news",
            env_key="BRAVE_API_KEY",
            search_fn=search_brave_news,
            group=ProviderGroup.specialized,
            requires_key=True,
        )
    )


# Initialize registry at module load
_init_provider_registry()
