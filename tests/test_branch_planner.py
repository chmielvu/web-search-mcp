"""Tests for search branch planning and provider sharding."""

from __future__ import annotations

from kindly_web_search_mcp_server.search.branch_planner import build_search_branch_specs
from kindly_web_search_mcp_server.search.provider_options import (
    ProviderOptionBundle,
    ProviderOptionSet,
)
from kindly_web_search_mcp_server.search.provider_plan import ProviderExecutionPlan
from kindly_web_search_mcp_server.search.query_rewrite_models import QueryVariant


def _plan_for(names: list[str]) -> ProviderExecutionPlan:
    bundles = {name: ProviderOptionBundle(provider_name=name) for name in names}
    return ProviderExecutionPlan(
        intent="general",
        policy_version="test",
        provider_names=tuple(names),
        search_options=None,
        options=ProviderOptionSet(bundles=bundles),
    )


def test_build_search_branch_specs_rewrite_disabled_uses_free_target() -> None:
    """Without rewrite, original query routes only to free providers."""
    plan = _plan_for(["searxng", "brave"])
    specs = build_search_branch_specs(
        intent="general",
        normalized_query="test brightdata speed",
        rewrite_variants=[],
        num_results=10,
        active_provider_names=["searxng", "brave"],
        provider_plan=plan,
    )
    assert len(specs) == 1
    assert specs[0].branch_type == "original_free"
    assert specs[0].providers == ["searxng"]


def test_build_search_branch_specs_routes_each_target() -> None:
    """Original, keyword, and neural variants use their target provider sets."""
    plan = _plan_for(["searxng", "brave", "tavily", "jina"])
    rewrite_variants = [
        QueryVariant(
            kind="keyword_refined",
            target="keyword",
            query="brightdata speed test",
            why="keyword rewrite",
            weight=1.0,
            branch_type="keyword_refined",
            reason="keyword rewrite",
            max_results=10,
        ),
        QueryVariant(
            kind="neural_refined",
            target="neural",
            query="Find information about Bright Data speed tests.",
            why="neural rewrite",
            weight=1.0,
            branch_type="neural_refined",
            reason="neural rewrite",
            max_results=10,
        ),
    ]
    specs = build_search_branch_specs(
        intent="general",
        normalized_query="test brightdata speed",
        rewrite_variants=rewrite_variants,
        num_results=10,
        active_provider_names=["searxng", "brave", "tavily", "jina"],
        provider_plan=plan,
    )
    assert [spec.branch_type for spec in specs] == [
        "original_free",
        "keyword_refined",
        "neural_refined",
    ]
    assert specs[0].providers == ["searxng"]
    assert specs[1].providers == ["searxng", "brave"]
    assert specs[2].providers == ["tavily", "jina"]


def test_literal_passthrough_detects_expert_syntax() -> None:
    from kindly_web_search_mcp_server.search.literal_passthrough import (
        detect_literal_passthrough,
    )

    assert detect_literal_passthrough('site:github.com "litellm" AND "cooldown"')
    assert not detect_literal_passthrough("how to grow tomatoes")


def test_build_search_branch_specs_emits_specialized_original_branch() -> None:
    plan = ProviderExecutionPlan(
        intent="news",
        policy_version="1.1",
        provider_names=("searxng", "brave", "brave_news", "telegram"),
        search_options=None,
        options=ProviderOptionSet(
            bundles={
                name: ProviderOptionBundle(provider_name=name)
                for name in ("searxng", "brave", "brave_news", "telegram")
            }
        ),
        specialized_provider_names=("telegram", "brave_news"),
    )
    specs = build_search_branch_specs(
        intent="news",
        normalized_query="openai layoffs",
        rewrite_variants=[],
        num_results=10,
        active_provider_names=["searxng", "brave", "brave_news", "telegram"],
        provider_plan=plan,
    )
    specialized = [spec for spec in specs if spec.branch_type == "specialized_original"]
    assert len(specialized) == 1
    assert specialized[0].query == "openai layoffs"
    assert specialized[0].providers == ["telegram", "brave_news"]
