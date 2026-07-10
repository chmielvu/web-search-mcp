from __future__ import annotations

from unittest import mock

from kindly_web_search_mcp_server.search.intent_policy import IntentSearchPolicy
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.provider_config import ProviderConfig, ProviderGroup
from kindly_web_search_mcp_server.search.provider_plan import (
    build_cache_identity,
    build_provider_execution_plan,
)


def _provider(name: str, group: ProviderGroup) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        env_key="",
        search_fn=lambda *args, **kwargs: [],  # noqa: ARG005
        group=group,
        requires_key=False,
    )


def test_provider_plan_selects_free_paid_and_specialized_from_policy(monkeypatch) -> None:
    fake_configs = {
        "searxng": _provider("searxng", ProviderGroup.free),
        "ddg": _provider("ddg", ProviderGroup.free),
        "brave": _provider("brave", ProviderGroup.paid_serp),
        "serpapi": _provider("serpapi", ProviderGroup.paid_serp),
        "serper": _provider("serper", ProviderGroup.paid_serp),
        "gemini": _provider("gemini", ProviderGroup.specialized),
        "github_graphql": _provider("github_graphql", ProviderGroup.specialized),
        "brave_news": _provider("brave_news", ProviderGroup.specialized),
    }
    policy = IntentSearchPolicy(
        intent="news",
        policy_version="1.1",
        specialized_providers=("brave_news",),
        provider_arguments={"brave": {"country": "us"}, "brave_news": {"freshness": "week"}},
        search_options_overrides={"searxng_pageno": 2},
        rewrite_temperature=0.0,
    )

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.provider_plan.get_provider_configs",
        lambda: fake_configs,
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.provider_plan.resolve_provider_configs",
        lambda names: [fake_configs[name] for name in names if name in fake_configs],
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.provider_plan.select_paid_serp_configs",
        lambda configs, limit=2: [fake_configs["brave"], fake_configs["serpapi"]],
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.provider_plan.resolve_intent_policy",
        lambda intent: policy,
    )

    plan = build_provider_execution_plan(intent="news", public_options=SearchOptions())

    assert plan.intent == "news"
    assert plan.policy_version == "1.1"
    assert "brave_news" in plan.provider_names
    assert plan.specialized_provider_names == ("brave_news",)
    assert plan.options.bundle_for("brave_news").arguments == {"freshness": "week"}


def test_cache_identity_changes_when_provider_arguments_change(monkeypatch) -> None:
    from kindly_web_search_mcp_server.search.provider_options import (
        ProviderOptionBundle,
        ProviderOptionSet,
    )
    from kindly_web_search_mcp_server.search.provider_plan import ProviderExecutionPlan

    def _identity(arguments: dict[str, object]) -> str:
        plan = ProviderExecutionPlan(
            intent="news",
            policy_version="1.1",
            provider_names=("brave", "brave_news"),
            search_options=None,
            options=ProviderOptionSet(
                bundles={
                    "brave": ProviderOptionBundle(provider_name="brave", arguments=arguments),
                    "brave_news": ProviderOptionBundle(provider_name="brave_news"),
                }
            ),
            specialized_provider_names=("brave_news",),
        )
        return build_cache_identity(
            query="headlines",
            intent="news",
            provider_plan=plan,
            search_options=None,
            rewrite_enabled=True,
        )

    assert _identity({}) != _identity({"goggles": ["https://example.com/goggle"]})


def test_cache_identity_bakes_intent_and_policy_version(monkeypatch) -> None:
    fake_plan = mock.Mock()
    fake_plan.policy_version = "1.0"
    fake_plan.plan_version = "1.0"
    fake_plan.provider_names = ("searxng", "brave")

    identity = build_cache_identity(
        query="FastAPI docs",
        intent="general",
        provider_plan=fake_plan,
        search_options=SearchOptions(),
        rewrite_enabled=True,
    )

    assert identity
