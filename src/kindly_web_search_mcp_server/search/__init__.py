"""Search providers: SearXNG (primary) + DDG (free fallback) → profile-driven providers.

Uses Reciprocal Rank Fusion (RRF) for multi-provider result merging.
Includes circuit breaker and budget tracking for provider health.

Provider selection is driven by the resolved search plan and explicit allow-lists.
"""

from __future__ import annotations

import logging
from .brave import search_brave
from .brightdata import search_brightdata
from .serpapi import search_serpapi
from .serper import search_serper
from .composio_llm_search import search_composio_llm_search
from .ddg import search_ddg
from .gemini_pollinations import search_gemini_pollinations
from .grok import search_grok_openrouter
from .github_graphql import search_github_graphql
from .hackernews import search_hackernews
from .google_cse import search_google_cse
from .jina import search_jina
from .qdrant import search_qdrant
from .reddit import search_reddit
from .stackexchange import search_stackexchange
from .provider_config import (
    ProviderConfig,
    ProviderGroup,
    register_provider,
    resolve_providers_for_search,
)
from .searxng import search_searxng
from .search_router import search_search_router
from .tavily import search_tavily
from .budget import ProviderBudget
from .circuit_breaker import CircuitBreaker  # deprecated, kept for test compat
from .errors import WebSearchProviderError
from .provider_execution import _search_single_provider
from .query_execution import search_single_query

LOGGER = logging.getLogger(__name__)

__all__ = [
    "CircuitBreaker",
    "ProviderBudget",
    "ProviderConfig",
    "WebSearchProviderError",
    "_search_single_provider",
    "resolve_providers_for_search",
    "search_single_query",
]


def _init_provider_registry() -> None:
    """Initialize provider registry with configured modes."""
    # Tier 1: Free providers (always fire, no semaphore)
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
            name="search_router",
            env_key="SEARCH_ROUTER_API_KEY",
            search_fn=search_search_router,
            group=ProviderGroup.serp_paid,
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

    # Tier 2: Paid SERP providers (always fire, semaphore-gated)
    register_provider(
        ProviderConfig(
            name="tavily",
            env_key="TAVILY_API_KEY",
            search_fn=search_tavily,
            group=ProviderGroup.other,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="brave",
            env_key="BRAVE_API_KEY",
            search_fn=search_brave,
            group=ProviderGroup.serp_paid,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="serper",
            env_key="SERPER_API_KEY",
            search_fn=search_serper,
            group=ProviderGroup.serp_paid,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="serpapi",
            env_key="SERPAPI_API_KEY",
            search_fn=search_serpapi,
            group=ProviderGroup.serp_paid,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="brightdata",
            env_key="BRIGHTDATA_API_KEY",
            search_fn=search_brightdata,
            group=ProviderGroup.serp_paid,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="google_cse",
            env_key="GOOGLE_API_KEY",
            search_fn=search_google_cse,
            group=ProviderGroup.free,
            requires_key=True,
            extra_env_keys=("GOOGLE_CSE_ENGINE_ID",),
        )
    )
    register_provider(
        ProviderConfig(
            name="jina",
            env_key="JINA_API_KEY",
            search_fn=search_jina,
            group=ProviderGroup.other,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="gemini",
            env_key="POLLINATIONS_API_KEY",
            search_fn=search_gemini_pollinations,
            group=ProviderGroup.other,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="grok_openrouter",
            env_key="OPENROUTER_API_KEY",
            search_fn=search_grok_openrouter,
            group=ProviderGroup.other,
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

    # Tier 3: Non-SERP providers (fire when named in INTENT_PROVIDERS[intent])
    register_provider(
        ProviderConfig(
            name="hackernews",
            env_key="",
            search_fn=search_hackernews,
            group=ProviderGroup.other,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="reddit",
            env_key="",
            search_fn=search_reddit,
            group=ProviderGroup.other,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="github_graphql",
            env_key="GITHUB_TOKEN",
            search_fn=search_github_graphql,
            group=ProviderGroup.other,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="stackexchange",
            env_key="STACKEXCHANGE_APP_KEY",
            search_fn=search_stackexchange,
            group=ProviderGroup.other,
            requires_key=False,
        )
    )


# Initialize registry at module load
_init_provider_registry()