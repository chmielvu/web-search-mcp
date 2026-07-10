"""Unit tests for intent-to-SearXNG-category routing."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.search.intent_policy import (
    resolve_intent_policy,
    _INTENT_POLICIES,
)
from kindly_web_search_mcp_server.search.intents import normalize_intent
from kindly_web_search_mcp_server.search.options import SearchOptions


class TestIntentToCategoryRouting:
    @pytest.mark.parametrize(
        "intent, expected_categories",
        [
            ("general", ("general", "it")),
            ("ai_coding_and_infrastructure", ("it",)),
            ("digital_humanities", ("it", "science")),
            ("comparison", ("general", "it")),
            ("social_media", ("general",)),
            ("news", ("news", "general")),
        ],
    )
    def test_intent_maps_to_correct_categories(self, intent, expected_categories):
        policy = _INTENT_POLICIES[normalize_intent(intent)]
        so = policy.apply_search_options(None)
        assert so is not None
        assert so.searxng_categories == expected_categories

    def test_resolve_intent_policy_uses_map(self):
        policy = resolve_intent_policy("ai_coding_and_infrastructure")
        assert policy.search_options_overrides == {"searxng_categories": ("it",)}

    def test_resolve_intent_policy_defaults_general(self):
        policy = resolve_intent_policy(None)
        assert policy.intent == "general"
        assert policy.search_options_overrides["searxng_categories"] == (
            "general",
            "it",
        )

    def test_policy_categories_override_user_categories(self):
        """Intent policy category overrides are applied to user options."""
        policy = resolve_intent_policy("ai_coding_and_infrastructure")
        user_so = SearchOptions(searxng_categories=("science",))
        so = policy.apply_search_options(user_so)
        assert so.searxng_categories == ("it",)

    def test_news_uses_freshness_and_tighter_rrf_k(self):
        news = resolve_intent_policy("news")
        assert news.freshness == "week"
        assert news.rrf_k == 35

    def test_digital_humanities_uses_wider_rrf_k(self):
        policy = resolve_intent_policy("digital_humanities")
        assert policy.freshness is None
        assert policy.rrf_k == 70


def test_news_policy_includes_brave_news_specialized_provider() -> None:
    news = resolve_intent_policy("news")
    assert news.policy_version == "1.1"
    assert "brave_news" in news.specialized_providers
    assert news.provider_arguments["brave_news"]["freshness"] == "week"


def test_resolve_intent_policy_merges_configured_goggles(monkeypatch) -> None:
    from kindly_web_search_mcp_server.settings import settings

    monkeypatch.setattr(
        settings,
        "brave_goggles_by_intent",
        {"social_media": ["https://example.com/social.goggle"]},
    )
    policy = resolve_intent_policy("social_media")
    assert policy.provider_arguments["brave"]["goggles"] == ["https://example.com/social.goggle"]
    assert "goggles" not in policy.provider_arguments.get("brightdata", {})


def test_resolve_intent_policy_leaves_goggles_empty_by_default() -> None:
    policy = resolve_intent_policy("general")
    assert "goggles" not in policy.provider_arguments.get("brave", {})
