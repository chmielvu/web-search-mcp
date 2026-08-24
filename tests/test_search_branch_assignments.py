from __future__ import annotations

from kindly_web_search_mcp_server.search.planning import (
    _FREE_CANDIDATES,
    _ORIGINAL_CANDIDATES,
    _SEMANTIC_EXA_CANDIDATES,
    _SEMANTIC_TAVILY_CANDIDATES,
    _SERP1_CANDIDATES,
    _SERP2_CANDIDATES,
)


def test_direct_branch_provider_assignments() -> None:
    assert _ORIGINAL_CANDIDATES == ("ddg", "qdrant", "searxng", "degoog")
    assert _FREE_CANDIDATES == ("ddg", "qdrant", "searxng", "degoog")
    assert _SERP1_CANDIDATES == ("brave",)
    assert _SERP2_CANDIDATES == ("brightdata", "serper", "search_router")
    assert _SEMANTIC_TAVILY_CANDIDATES == ("tavily", "langsearch")
    assert _SEMANTIC_EXA_CANDIDATES == ("exa",)
