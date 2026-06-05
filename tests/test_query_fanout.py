from __future__ import annotations

from kindly_web_search_mcp_server.search.query_fanout import (
    FANOUT_JSON_SCHEMA,
    build_fanout_messages,
    parse_fanout_output,
    normalize_fanout_output,
)
from kindly_web_search_mcp_server.search.query_rewrite_models import (
    QueryFanoutOutput,
    QueryVariant,
)


def test_build_fanout_messages_include_context_and_schema() -> None:
    messages = build_fanout_messages(
        query="React 19 vs Vue 4 SSR performance and developer experience",
        research_goal="compare frameworks",
        must_keep_terms=["React 19", "Vue 4"],
        intent="comparison",
        active_provider_names=["searxng", "gemini", "stackexchange"],
        routing={"keyword": True, "neural": True, "community": True},
    )

    assert messages[0]["role"] == "system"
    assert "8 to 10 branches" in messages[0]["content"]
    assert "React 19" in messages[1]["content"]
    assert "stackexchange" in messages[1]["content"]
    assert FANOUT_JSON_SCHEMA["properties"]["branches"]["minItems"] == 8
    assert FANOUT_JSON_SCHEMA["properties"]["branches"]["maxItems"] == 10


def test_normalize_fanout_output_dedupes_and_merges_controls() -> None:
    output = QueryFanoutOutput(
        rationale="  broader branch plan  ",
        branches=[
            QueryVariant(
                kind="related",
                target="keyword",
                query="  FastMCP docs  ",
                why=" docs ",
                weight=1.0,
                branch_type="related",
                must_keep_terms=["FastMCP"],
                max_results=4,
                reason=" docs ",
            ),
            QueryVariant(
                kind="related",
                target="keyword",
                query="FastMCP docs",
                why="duplicate",
                weight=1.0,
                branch_type="related",
                must_keep_terms=["FastMCP", "docs"],
                max_results=4,
                reason="duplicate",
            ),
            QueryVariant(
                kind="comparative",
                target="neural",
                query="FastMCP vs LangChain branching",
                why=" compare ",
                weight=0.9,
                branch_type="comparative",
                must_keep_terms=["LangChain"],
                max_results=5,
                reason=" compare ",
            ),
        ],
    )

    normalized = normalize_fanout_output(
        output,
        must_keep_terms=["FastMCP"],
        max_branches=10,
    )

    assert normalized.rationale == "broader branch plan"
    assert [branch.query for branch in normalized.branches] == [
        "FastMCP docs",
        "FastMCP vs LangChain branching",
    ]
    assert normalized.branches[0].must_keep_terms == ["FastMCP"]
    assert normalized.branches[1].must_keep_terms == ["FastMCP", "LangChain"]
    assert normalized.branches[1].branch_type == "comparative"


def test_parse_fanout_output_rejects_too_few_branches() -> None:
    import pytest

    with pytest.raises(ValueError, match="8 to 10 branches"):
        parse_fanout_output(
            "{\"rationale\":\"ok\",\"branches\":[{\"kind\":\"related\",\"branch_type\":\"related\",\"target\":\"keyword\",\"query\":\"a\",\"why\":\"a\",\"reason\":\"a\",\"weight\":1.0,\"must_keep_terms\":[],\"max_results\":3}]}"
        )
