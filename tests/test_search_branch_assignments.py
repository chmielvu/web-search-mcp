from __future__ import annotations

from kindly_web_search_mcp_server.heuristics.query_features import build_query_features
from kindly_web_search_mcp_server.search.planning import (
    _FREE_CANDIDATES,
    _ORIGINAL_CANDIDATES,
    _SEMANTIC_EXA_CANDIDATES,
    _SEMANTIC_TAVILY_CANDIDATES,
    _SERP1_CANDIDATES,
    _SERP2_CANDIDATES,
    _branch_fallback_queries,
    _keyword_query,
)


def test_direct_branch_provider_assignments() -> None:
    assert _ORIGINAL_CANDIDATES == ("ddg", "qdrant", "searxng", "degoog")
    assert _FREE_CANDIDATES == ("ddg", "qdrant", "searxng", "degoog")
    assert _SERP1_CANDIDATES == ("brave",)
    assert _SERP2_CANDIDATES == ("brightdata", "serper", "search_router")
    assert _SEMANTIC_TAVILY_CANDIDATES == ("tavily", "langsearch")
    assert _SEMANTIC_EXA_CANDIDATES == ("exa",)


def test_fallback_queries_no_goal_fusion() -> None:
    """Fallback slots never embed the research goal text."""
    features = build_query_features("FastMCP SSE vs Streamable HTTP transport differences")
    free_fb, serp1_fb, serp2_fb, tavily_fb, exa_fb = _branch_fallback_queries(
        features,
        terms=(),
        suggestions=(),
        current_year="2026",
        exact=False,
    )
    for slot in (free_fb, serp1_fb, serp2_fb, tavily_fb, exa_fb):
        assert "Find official docs" not in slot
        assert "Compare MCP server transports" not in slot


def test_fallback_queries_exact_mode_equals_base() -> None:
    """Exact mode: every slot is the normalized base query."""
    features = build_query_features("PostgreSQL vacuum autovacuum tuning")
    slots = _branch_fallback_queries(
        features,
        terms=("vacuum", "autovacuum"),
        suggestions=("postgresql vacuum",),
        current_year="2026",
        exact=True,
    )
    base = features.cleaned or features.raw
    assert slots == (base, base, base, base, base)


def test_keyword_query_skips_word_subsumed_terms() -> None:
    """A term whose words are all already in the base is not re-appended."""
    base = "official gemini api documentation"
    out = _keyword_query(base, ("official gemini api", "streaming"))
    assert out == "official gemini api documentation streaming"
