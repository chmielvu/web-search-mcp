"""Static provider definition catalog (no branch routing metadata)."""

from __future__ import annotations

from pydantic import Field

from ..settings import settings
from .contracts import ContractModel


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
)
