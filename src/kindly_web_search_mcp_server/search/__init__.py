"""Search providers: SearXNG (primary) + DDG (free fallback) → Paid providers (conditional).

Uses Reciprocal Rank Fusion (RRF) for multi-provider result merging.
Includes circuit breaker and budget tracking for provider health.

Provider modes control when providers fire:
- ALWAYS: Free providers (SearXNG, DDG) always fire
- CONDITIONAL: Paid providers only fire when caller requests via providers param
- NEVER: Disabled providers never fire
"""

from __future__ import annotations

import logging

from ..settings import settings
from .brave import search_brave
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
    ProviderMode,
    parse_provider_mode,
    register_provider,
    resolve_providers_for_search,
)
from .searxng import search_searxng
from .search_router import search_search_router
from .tavily import search_tavily
from .budget import ProviderBudget
from .circuit_breaker import CircuitBreaker
from .errors import WebSearchProviderError
from .provider_execution import _search_single_provider
from .query_execution import search_single_query

LOGGER = logging.getLogger(__name__)

__all__ = [
    "CircuitBreaker",
    "ProviderBudget",
    "ProviderConfig",
    "ProviderMode",
    "WebSearchProviderError",
    "_search_single_provider",
    "resolve_providers_for_search",
    "search_single_query",
]

# =============================================================================
# Provider Registry
# =============================================================================

def _parse_mode(mode_str: str) -> ProviderMode:
    """Parse mode string to ProviderMode. Defaults to ALWAYS if invalid."""
    parsed = parse_provider_mode(mode_str)
    return parsed if parsed else ProviderMode.ALWAYS


def _init_provider_registry() -> None:
    """Initialize provider registry with configured modes."""
    # Tier 1: Free providers (default always, configurable via env)
    register_provider(
        ProviderConfig(
            name="searxng",
            mode=ProviderMode.ALWAYS,
            env_key="SEARXNG_BASE_URL",
            search_fn=search_searxng,
            is_free=True,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="ddg",
            mode=_parse_mode(settings.ddg_mode),  # default "always" in settings.py
            env_key="",  # No env key needed
            search_fn=search_ddg,
            is_free=True,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="search_router",
            mode=ProviderMode.ALWAYS,
            env_key="SEARCH_ROUTER_API_KEY",
            search_fn=search_search_router,
            is_free=True,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="qdrant",
            mode=ProviderMode.ALWAYS,
            env_key="KINDLY_QDRANT_SPACE_URL",
            search_fn=search_qdrant,
            is_free=True,
            requires_key=False,
        )
    )

    # Tier 2: Paid providers (mode from settings.py defaults)
    register_provider(
        ProviderConfig(
            name="tavily",
            mode=_parse_mode(settings.tavily_mode),  # default "never" in settings.py
            env_key="TAVILY_API_KEY",
            search_fn=search_tavily,
            is_free=False,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="brave",
            mode=_parse_mode(settings.brave_mode),  # default "always" in settings.py
            env_key="BRAVE_API_KEY",
            search_fn=search_brave,
            is_free=False,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="google_cse",
            mode=ProviderMode.ALWAYS,
            env_key="KINDLY_GOOGLE_CSE_API_KEY",
            search_fn=search_google_cse,
            is_free=False,
            requires_key=True,
            extra_env_keys=("KINDLY_GOOGLE_CSE_ENGINE_ID",),
        )
    )
    register_provider(
        ProviderConfig(
            name="jina",
            mode=_parse_mode(
                settings.jina_mode
            ),  # default "conditional" in settings.py
            env_key="JINA_API_KEY",
            search_fn=search_jina,
            is_free=False,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="gemini",
            mode=_parse_mode(settings.gemini_mode),  # default "always" in settings.py
            env_key="POLLINATIONS_API_KEY",
            search_fn=search_gemini_pollinations,
            is_free=False,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="grok_openrouter",
            mode=_parse_mode(
                settings.grok_web_search_mode
            ),  # default "conditional" in settings.py
            env_key="OPENROUTER_API_KEY",
            search_fn=search_grok_openrouter,
            is_free=False,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="composio_llm_search",
            mode=_parse_mode(
                settings.composio_llm_search_mode
            ),  # default "always" in settings.py
            env_key="COMPOSIO_API_KEY",
            search_fn=search_composio_llm_search,
            is_free=False,
            requires_key=True,
            extra_env_keys=("KINDLY_COMPOSIO_USER_ID",),
        )
    )

    # Tier 3: Community providers (CONDITIONAL — only fire when explicitly requested)
    register_provider(
        ProviderConfig(
            name="hackernews",
            mode=ProviderMode.CONDITIONAL,
            env_key="",
            search_fn=search_hackernews,
            is_free=True,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="reddit",
            mode=ProviderMode.CONDITIONAL,
            env_key="",
            search_fn=search_reddit,
            is_free=True,
            requires_key=False,
        )
    )
    register_provider(
        ProviderConfig(
            name="github_graphql",
            mode=ProviderMode.CONDITIONAL,
            env_key="GITHUB_TOKEN",
            search_fn=search_github_graphql,
            is_free=True,
            requires_key=True,
        )
    )
    register_provider(
        ProviderConfig(
            name="stackexchange",
            mode=ProviderMode.CONDITIONAL,
            env_key="STACKEXCHANGE_APP_KEY",
            search_fn=search_stackexchange,
            is_free=True,
            requires_key=False,
        )
    )


# Initialize registry at module load
_init_provider_registry()
