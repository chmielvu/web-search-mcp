"""Integration tests for Phase Two plan → branch wiring."""

from __future__ import annotations

from kindly_web_search_mcp_server.search.branch_planner import build_search_branch_specs
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.provider_plan import build_provider_execution_plan


def test_news_plan_schedules_specialized_original_with_brave_news() -> None:
    plan = build_provider_execution_plan(intent="news", public_options=SearchOptions())
    assert plan.intent == "news"
    assert plan.policy_version == "1.1"
    assert "brave_news" in plan.specialized_provider_names

    brave_news_bundle = plan.options.bundle_for("brave_news")
    assert brave_news_bundle is not None
    assert brave_news_bundle.arguments.get("freshness") == "week"

    specs = build_search_branch_specs(
        intent="news",
        normalized_query="openai layoffs",
        rewrite_variants=[],
        num_results=10,
        active_provider_names=list(plan.provider_names),
        provider_plan=plan,
    )
    specialized = [spec for spec in specs if spec.branch_type == "specialized_original"]
    assert len(specialized) == 1
    assert "brave_news" in specialized[0].providers
    assert specialized[0].query == "openai layoffs"


def test_general_plan_does_not_emit_specialized_original_branch() -> None:
    plan = build_provider_execution_plan(intent="general", public_options=SearchOptions())
    assert plan.specialized_provider_names == ()

    specs = build_search_branch_specs(
        intent="general",
        normalized_query="fastapi docs",
        rewrite_variants=[],
        num_results=10,
        active_provider_names=list(plan.provider_names),
        provider_plan=plan,
    )
    assert not any(spec.branch_type == "specialized_original" for spec in specs)
