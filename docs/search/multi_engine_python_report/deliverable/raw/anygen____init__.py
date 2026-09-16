"""hsearch — unified search over 6 commercial search APIs.

Quick start::

    from hsearch import search_sync, SearchResult

    resp = search_sync("Python tutorial", mode="general", top=5)
    for r in resp.results:
        print(r.title, r.url)

Async::

    from hsearch import search

    resp = await search("Python tutorial", providers=["tavily", "brave"])
"""

__version__ = "1.0.0"

from hsearch.engine import (
    AgentResponse,
    AnswerResponse,
    ExtractResult,
    GroundingResponse,
    ResearchResponse,
    SearchResponse,
    TraversalResponse,
    agent,
    agent_sync,
    answer,
    answer_sync,
    crawl_site,
    crawl_site_sync,
    extract_urls,
    extract_urls_sync,
    find_similar,
    find_similar_sync,
    ground,
    ground_sync,
    map_site,
    map_site_sync,
    research,
    research_streaming,
    research_sync,
    account_usage,
    account_usage_sync,
    search,
    search_sync,
)
from hsearch.filters import Filters
from hsearch.models import SearchResult

__all__ = [
    "AgentResponse",
    "AnswerResponse",
    "ExtractResult",
    "Filters",
    "GroundingResponse",
    "ResearchResponse",
    "SearchResponse",
    "SearchResult",
    "TraversalResponse",
    "__version__",
    "agent",
    "agent_sync",
    "answer",
    "answer_sync",
    "crawl_site",
    "crawl_site_sync",
    "map_site",
    "map_site_sync",
    "extract_urls",
    "extract_urls_sync",
    "find_similar",
    "find_similar_sync",
    "ground",
    "ground_sync",
    "research",
    "research_streaming",
    "research_sync",
    "account_usage",
    "account_usage_sync",
    "search",
    "search_sync",
]
