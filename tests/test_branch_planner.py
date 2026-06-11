from __future__ import annotations


def test_branch_planner_keeps_original_and_caps_rewrites() -> None:
    from kindly_web_search_mcp_server.search.branch_planner import (
        build_search_branch_specs,
    )
    from kindly_web_search_mcp_server.search.context import SearchContext
    from kindly_web_search_mcp_server.search.options import SearchOptions
    from kindly_web_search_mcp_server.search.profiles.resolve import resolve_search_profile
    from kindly_web_search_mcp_server.search.provider_plan import (
        build_provider_execution_plan,
    )
    from kindly_web_search_mcp_server.search.query_rewrite_models import QueryVariant

    profile = resolve_search_profile("general")
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
        providers=("searxng", "brave", "google_cse"),
        num_results=5,
        search_options=SearchOptions(),
        profile_name="general",
    )
    provider_plan = build_provider_execution_plan(
        profile=profile,
        context=context,
        public_options=context.search_options,
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
    assert specs[0].provider_options_by_name is provider_plan.options.bundles
