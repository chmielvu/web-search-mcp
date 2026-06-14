from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

import httpx

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.context import SearchContext
from kindly_web_search_mcp_server.search import provider_plan as provider_plan_module
from kindly_web_search_mcp_server.search import _search_single_provider
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.profiles.models import SearchProfile
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


def test_provider_plan_uses_requested_providers_and_profile_weights(
    monkeypatch,
) -> None:
    fake_configs = [
        _provider("searxng", ProviderGroup.free),
        _provider("brave", ProviderGroup.serp_paid),
        _provider("google_cse", ProviderGroup.free),
    ]
    monkeypatch.setattr(
        provider_plan_module,
        "resolve_provider_configs",
        lambda provider_names, intent="general": fake_configs,  # noqa: ARG005
    )

    profile = SearchProfile(
        name="general",
        provider_weights={"searxng": 1.0, "brave": 1.0, "google_cse": 1.0},
        provider_names=("searxng", "brave", "google_cse"),
    )
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
        profile_name="general",
    )

    plan = build_provider_execution_plan(
        profile=profile,
        intent=context.intent,
        public_options=context.search_options,
    )

    assert plan.provider_names == ("searxng", "brave", "google_cse")
    assert set(plan.options.bundles) == {"searxng", "brave", "google_cse"}
    assert build_cache_identity(
        query=context.normalized_query,
        profile=profile,
        provider_plan=plan,
        search_options=context.search_options,
        rewrite_enabled=True,
    )


def test_provider_plan_carries_profile_provider_arguments(monkeypatch) -> None:
    fake_configs = [_provider("brave", ProviderGroup.serp_paid)]
    monkeypatch.setattr(
        provider_plan_module,
        "resolve_provider_configs",
        lambda provider_names, intent="general": fake_configs,  # noqa: ARG005
    )

    profile = SearchProfile(
        name="general",
        provider_weights={"brave": 1.0},
        provider_names=("brave",),
        provider_arguments={"brave": {"country": "us", "safe": True}},
    )
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
        profile_name="general",
    )

    plan = build_provider_execution_plan(
        profile=profile,
        intent=context.intent,
        public_options=context.search_options,
    )

    assert plan.options.bundle_for("brave") is not None
    assert plan.options.bundle_for("brave").arguments == {
        "country": "us",
        "safe": True,
    }
    assert plan.provider_names == ("brave",)


class TestProviderArgumentsForwarding(IsolatedAsyncioTestCase):
    async def test_search_single_provider_forwards_supported_arguments(self) -> None:
        captured: dict[str, object] = {}

        async def provider_fn(
            query: str,
            *,
            num_results: int,
            http_client: httpx.AsyncClient,
            search_options: SearchOptions | None = None,
            country: str | None = None,
        ) -> list[WebSearchResult]:
            captured["query"] = query
            captured["num_results"] = num_results
            captured["search_options"] = search_options
            captured["country"] = country
            return [
                WebSearchResult(
                    title="Result",
                    link="https://example.com",
                    snippet="Snippet",
                )
            ]

        async with httpx.AsyncClient() as client:
            results = await _search_single_provider(
                "custom",
                provider_fn,
                "FastAPI docs",
                1,
                client,
                SearchOptions(),
                None,
                {"country": "us", "ignored": "value"},
            )

        assert len(results) == 1
        assert captured["query"] == "FastAPI docs"
        assert captured["num_results"] == 1
        assert captured["country"] == "us"
        assert isinstance(captured["search_options"], SearchOptions)
