"""Tests for search branch planning and provider sharding."""

from __future__ import annotations

from kindly_web_search_mcp_server.search.branch_planner import (
    _shard_providers,
    build_search_branch_specs,
)
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
        provider_weights={name: 1.0 for name in names},
        search_options=None,
        options=ProviderOptionSet(bundles=bundles),
    )


def test_shard_providers_no_duplication() -> None:
    """Each provider must appear in exactly one branch."""
    providers = ["brightdata", "brave", "searxng"]
    shards = _shard_providers(providers, branch_count=2)
    flat = [p for shard in shards for p in shard]
    assert sorted(flat) == sorted(providers)
    assert len(flat) == len(providers)


def test_shard_providers_single_branch_returns_all() -> None:
    providers = ["brightdata", "brave"]
    shards = _shard_providers(providers, branch_count=1)
    assert shards == [providers]


def test_shard_providers_more_branches_than_providers() -> None:
    providers = ["brightdata"]
    shards = _shard_providers(providers, branch_count=3)
    assert shards == [["brightdata"], [], []]


def test_build_search_branch_specs_rewrite_disabled_single_branch() -> None:
    """When rewrite is disabled, only the canonical original branch is created."""
    plan = _plan_for(["brightdata", "brave"])
    specs = build_search_branch_specs(
        intent="general",
        normalized_query="test brightdata speed",
        rewrite_variants=[],
        num_results=10,
        active_provider_names=["brightdata", "brave"],
        provider_plan=plan,
    )
    assert len(specs) == 1
    assert specs[0].branch_type == "original"
    assert set(specs[0].providers or []) == {"brightdata", "brave"}


def test_build_search_branch_specs_rewrite_enabled_distributes_providers() -> None:
    """Rewrite variants create multiple branches without duplicating providers."""
    plan = _plan_for(["brightdata", "brave", "searxng"])
    rewrite_variants = [
        QueryVariant(
            kind="rewrite",
            target="keyword",
            query="brightdata speed test",
            why="test rewrite",
            weight=1.0,
            branch_type="rewrite",
            reason="test rewrite",
            max_results=10,
        )
    ]
    specs = build_search_branch_specs(
        intent="general",
        normalized_query="test brightdata speed",
        rewrite_variants=rewrite_variants,
        num_results=10,
        active_provider_names=["brightdata", "brave", "searxng"],
        provider_plan=plan,
    )
    assert len(specs) == 2  # original + 1 rewrite
    flat = [p for spec in specs for p in spec.providers or []]
    assert sorted(flat) == sorted(["brightdata", "brave", "searxng"])
    assert len(flat) == len(set(flat))
