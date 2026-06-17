from __future__ import annotations


def test_branch_planner_keeps_original_and_caps_rewrites() -> None:
    from kindly_web_search_mcp_server.search.branch_planner import (
        build_search_branch_specs,
    )
    from kindly_web_search_mcp_server.search.context import SearchContext
    from kindly_web_search_mcp_server.search.options import SearchOptions
    from kindly_web_search_mcp_server.search.provider_options import ProviderOptionSet
    from kindly_web_search_mcp_server.search.provider_plan import ProviderExecutionPlan
    from kindly_web_search_mcp_server.search.query_rewrite_models import QueryVariant

    context = SearchContext(
        raw_query="FastAPI docs",
        normalized_query="FastAPI docs",
        research_goal=None,
        session_id="session-1",
        intent="general",
        confidence=0.9,
        should_decompose=False,
        rationale="clear request",
        entities=(),
        must_keep_terms=(),
        num_results=5,
        search_options=SearchOptions(),
    )
    provider_plan = ProviderExecutionPlan(
        intent="general",
        policy_version="1.0",
        provider_names=("searxng", "brave"),
        provider_weights={"searxng": 1.0, "brave": 1.0},
        search_options=SearchOptions(),
        options=ProviderOptionSet(bundles={}),
    )
    rewrite_variants = [
        QueryVariant(
            kind="rewrite",
            target="keyword",
            query="first rewrite",
            why="first",
            weight=0.9,
            max_results=3,
        ),
        QueryVariant(
            kind="rewrite",
            target="keyword",
            query="second rewrite",
            why="second",
            weight=0.8,
            max_results=3,
        ),
        QueryVariant(
            kind="rewrite",
            target="keyword",
            query="third rewrite",
            why="third",
            weight=0.7,
            max_results=3,
        ),
    ]

    specs = build_search_branch_specs(
        intent=context.intent,
        normalized_query=context.normalized_query,
        rewrite_variants=rewrite_variants,
        num_results=5,
        active_provider_names=["searxng", "brave", "google_cse", "jina"],
        provider_plan=provider_plan,
    )

    assert [spec.query for spec in specs] == [
        "FastAPI docs",
        "first rewrite",
        "second rewrite",
    ]
    assert specs[0].branch_type == "original"
    assert specs[0].weight == 1.0
    assert specs[0].max_results == 5
    assert specs[0].intent == "general"
