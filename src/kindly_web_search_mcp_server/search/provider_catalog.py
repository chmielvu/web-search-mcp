"""Static provider definition catalog (no branch routing metadata)."""

from __future__ import annotations

from pydantic import Field

from ..settings import settings
from .contracts import ContractModel, ProviderGroup


class ProviderDefinition(ContractModel):
    name: str
    group: ProviderGroup
    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    description: str
    default_timeout_seconds: float = Field(gt=0)
    requires_embedding: bool = False


def _definition(
    name: str,
    group: ProviderGroup,
    description: str,
    *,
    all_of: tuple[str, ...] = (),
    timeout: float = 10.0,
    requires_embedding: bool = False,
) -> ProviderDefinition:
    return ProviderDefinition(
        name=name,
        group=group,
        all_of=all_of,
        description=description,
        default_timeout_seconds=timeout,
        requires_embedding=requires_embedding,
    )


def brightdata_provider_call_timeout_seconds() -> float:
    return settings.search_retrieve_budget_seconds


PROVIDER_DEFINITIONS_LIST: tuple[ProviderDefinition, ...] = (
    _definition(
        "searxng",
        ProviderGroup.FREE,
        "SearXNG metasearch",
        all_of=("SEARXNG_BASE_URL",),
    ),
    _definition("ddg", ProviderGroup.FREE, "DuckDuckGo search"),
    _definition("gemma", ProviderGroup.FREE, "Gemini grounded search"),
    _definition(
        "degoog",
        ProviderGroup.FREE,
        "DeGoog search",
        all_of=("DEGOOG_BASE_URL",),
    ),
    _definition(
        "qdrant",
        ProviderGroup.FREE,
        "Qdrant web index",
        all_of=("QDRANT_SPACE_URL",),
        requires_embedding=True,
    ),
    _definition(
        "composio_llm_search",
        ProviderGroup.FREE,
        "Composio LLM search",
        all_of=("COMPOSIO_API_KEY", "COMPOSIO_USER_ID"),
    ),
    _definition(
        "search_router",
        ProviderGroup.PAID_SERP,
        "Search Router",
        all_of=("SEARCH_ROUTER_API_KEY",),
    ),
    _definition(
        "brave",
        ProviderGroup.PAID_SERP,
        "Brave LLM Context",
        all_of=("BRAVE_API_KEY",),
    ),
    _definition(
        "serper",
        ProviderGroup.PAID_SERP,
        "Serper",
        all_of=("SERPER_API_KEY",),
    ),
    _definition(
        "serpapi",
        ProviderGroup.PAID_SERP,
        "SerpAPI",
        all_of=("SERPAPI_API_KEY",),
    ),
    _definition(
        "brightdata",
        ProviderGroup.PAID_SERP,
        "Bright Data Google",
        all_of=("BRIGHTDATA_API_KEY",),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "brightdata_bing",
        ProviderGroup.PAID_SERP,
        "Bright Data Bing",
        all_of=("BRIGHTDATA_API_KEY",),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "brightdata_yandex",
        ProviderGroup.PAID_SERP,
        "Bright Data Yandex",
        all_of=("BRIGHTDATA_API_KEY",),
        timeout=brightdata_provider_call_timeout_seconds(),
    ),
    _definition(
        "tavily",
        ProviderGroup.SPECIALIZED,
        "Tavily",
        all_of=("TAVILY_API_KEY",),
    ),
    _definition(
        "jina",
        ProviderGroup.SPECIALIZED,
        "Jina search",
        all_of=("JINA_API_KEY",),
    ),
    _definition(
        "grok_openrouter",
        ProviderGroup.SPECIALIZED,
        "Grok via OpenRouter",
        all_of=("OPENROUTER_API_KEY",),
    ),
    _definition("hackernews", ProviderGroup.SPECIALIZED, "Hacker News"),
    _definition("reddit", ProviderGroup.SPECIALIZED, "Reddit"),
    _definition(
        "github_graphql",
        ProviderGroup.SPECIALIZED,
        "GitHub GraphQL",
        all_of=("GITHUB_TOKEN",),
    ),
    _definition(
        "telegram",
        ProviderGroup.SPECIALIZED,
        "Telegram",
        all_of=("TELEGRAM_API_ID", "TELEGRAM_API_HASH"),
    ),
    _definition(
        "brave_news",
        ProviderGroup.SPECIALIZED,
        "Brave News",
        all_of=("BRAVE_API_KEY",),
    ),
)
