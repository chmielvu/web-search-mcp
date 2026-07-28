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
    specialized: bool = False


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
    specialized: bool = False,
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
        specialized=specialized,
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
    ),
    _definition("ddg", "providers.ddg", "search_ddg", "DuckDuckGo search"),
    _definition(
        "gemma",
        "providers.gemma_serp",
        "search_gemma",
        "Pollinations Gemini Fast search",
        all_of=("POLLINATIONS_API_KEY",),
        timeout=settings.search_retrieve_budget_seconds,
    ),
    _definition(
        "degoog",
        "providers.degoog",
        "search_degoog",
        "DeGoog search",
        all_of=("DEGOOG_BASE_URL",),
    ),
    _definition(
        "qdrant",
        "providers.qdrant",
        "search_qdrant",
        "Qdrant web index",
        all_of=("QDRANT_SPACE_URL",),
        requires_embedding=True,
    ),
    _definition(
        "composio_llm_search",
        "providers.composio_llm_search",
        "search_composio_llm_search",
        "Composio LLM search",
        all_of=("COMPOSIO_API_KEY", "COMPOSIO_USER_ID"),
    ),
    _definition(
        "search_router",
        "providers.search_router",
        "search_search_router",
        "Search Router",
        all_of=("SEARCH_ROUTER_API_KEY",),
    ),
    _definition(
        "brave",
        "providers.brave",
        "search_brave",
        "Brave LLM Context",
        all_of=("BRAVE_API_KEY",),
    ),
    _definition(
        "serper",
        "providers.serper",
        "search_serper",
        "Serper",
        all_of=("SERPER_API_KEY",),
    ),
    _definition(
        "serpapi",
        "providers.serpapi",
        "search_serpapi",
        "SerpAPI",
        all_of=("SERPAPI_API_KEY",),
    ),
    _definition(
        "brightdata",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Google",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "brightdata_bing",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Bing",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "brightdata_yandex",
        "providers.brightdata",
        "search_brightdata",
        "Bright Data Yandex",
        all_of=("BRIGHTDATA_API_KEY",),
        any_of=("BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_ZONE"),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "tavily",
        "providers.tavily",
        "search_tavily",
        "Tavily",
        all_of=("TAVILY_API_KEY",),
        specialized=True,
    ),
    _definition(
        "jina",
        "providers.jina",
        "search_jina",
        "Jina search",
        all_of=("JINA_API_KEY",),
        specialized=True,
    ),
    _definition(
        "langsearch",
        "providers.langsearch",
        "search_langsearch",
        "LangSearch AI web search",
        all_of=("LANGSEARCH_API_KEY",),
    ),
    _definition(
        "grok_xai",
        "providers.grok",
        "search_grok_xai",
        "Grok native xAI web and X search",
        all_of=("XAI_API_KEY",),
        specialized=True,
    ),
    _definition(
        "hackernews", "providers.hackernews", "search_hackernews", "Hacker News", specialized=True
    ),
    _definition("reddit", "providers.reddit", "search_reddit", "Reddit", specialized=True),
    _definition(
        "github",
        "providers.github",
        "search_github",
        "GitHub code, Issues, and Discussions",
        specialized=True,
    ),
    _definition(
        "sourcegraph",
        "providers.sourcegraph",
        "search_sourcegraph",
        "Sourcegraph public code search",
        specialized=True,
    ),
    _definition(
        "gitlab",
        "providers.gitlab",
        "search_gitlab",
        "GitLab public code search",
        specialized=True,
    ),
    _definition(
        "telegram",
        "providers.telegram",
        "search_telegram",
        "Telegram",
        all_of=("TELEGRAM_API_ID", "TELEGRAM_API_HASH"),
        specialized=True,
    ),
    _definition(
        "brave_news",
        "providers.brave_news",
        "search_brave_news",
        "Brave News",
        all_of=("BRAVE_API_KEY",),
        specialized=True,
    ),
)
