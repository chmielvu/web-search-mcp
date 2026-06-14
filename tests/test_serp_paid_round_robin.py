from __future__ import annotations

from kindly_web_search_mcp_server.search import provider_config as pc
from kindly_web_search_mcp_server.search import provider_plan as pp
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.profiles.models import SearchProfile
from kindly_web_search_mcp_server.search.provider_config import ProviderGroup
from kindly_web_search_mcp_server.search.provider_plan import build_cache_identity


def _provider(name: str, group: ProviderGroup) -> pc.ProviderConfig:
    return pc.ProviderConfig(
        name=name,
        env_key="",
        search_fn=lambda *args, **kwargs: [],  # noqa: ARG005
        group=group,
        requires_key=False,
    )


def test_select_serp_paid_configs_rotates_across_calls(monkeypatch) -> None:
    monkeypatch.setattr(pc, "_SERP_PAID_RR_CURSOR", 0)
    configs = [
        _provider("searxng", ProviderGroup.free),
        _provider("brave", ProviderGroup.serp_paid),
        _provider("serpapi", ProviderGroup.serp_paid),
        _provider("serper", ProviderGroup.serp_paid),
    ]

    first = [config.name for config in pc.select_serp_paid_configs(configs, limit=2)]
    second = [config.name for config in pc.select_serp_paid_configs(configs, limit=2)]
    third = [config.name for config in pc.select_serp_paid_configs(configs, limit=2)]

    assert first == ["brave", "serpapi"]
    assert second == ["serper", "brave"]
    assert third == ["serpapi", "serper"]


def test_build_provider_execution_plan_rotates_paid_subset(monkeypatch) -> None:
    monkeypatch.setattr(pc, "_SERP_PAID_RR_CURSOR", 0)
    fake_configs = [
        _provider("searxng", ProviderGroup.free),
        _provider("brave", ProviderGroup.serp_paid),
        _provider("serpapi", ProviderGroup.serp_paid),
        _provider("serper", ProviderGroup.serp_paid),
        _provider("hackernews", ProviderGroup.other),
    ]
    monkeypatch.setattr(
        pp,
        "resolve_provider_configs",
        lambda provider_names, intent="general": fake_configs,  # noqa: ARG005
    )
    monkeypatch.setattr(
        pp,
        "resolve_providers_for_search",
        lambda intent="general": fake_configs,  # noqa: ARG005
    )

    profile = SearchProfile(
        name="general",
        provider_weights={name: 1.0 for name in ("searxng", "brave", "serpapi", "serper", "hackernews")},
        provider_names=("searxng", "brave", "serpapi", "serper", "hackernews"),
    )
    options = SearchOptions()

    first = pp.build_provider_execution_plan(
        profile=profile,
        intent="general",
        public_options=options,
    )
    second = pp.build_provider_execution_plan(
        profile=profile,
        intent="general",
        public_options=options,
    )

    assert first.provider_names == (
        "searxng",
        "brave",
        "serpapi",
        "hackernews",
    )
    assert second.provider_names == (
        "searxng",
        "serper",
        "brave",
        "hackernews",
    )
    assert set(first.options.bundles) == set(first.provider_names)
    assert set(second.options.bundles) == set(second.provider_names)
    assert (
        build_cache_identity(
            query="FastAPI docs",
            profile=profile,
            provider_plan=first,
            search_options=options,
            rewrite_enabled=True,
        )
        != build_cache_identity(
            query="FastAPI docs",
            profile=profile,
            provider_plan=second,
            search_options=options,
            rewrite_enabled=True,
        )
    )
