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

    def test_intents_share_the_global_rrf_setting(self):
        news = resolve_intent_policy("news")
        digital_humanities = resolve_intent_policy("digital_humanities")
        assert news.freshness == "week"
        assert not hasattr(news, "rrf_k")
        assert not hasattr(digital_humanities, "rrf_k")




def test_resolve_intent_policy_merges_configured_goggles(monkeypatch) -> None:
    import kindly_web_search_mcp_server.search.intent_policy as intent_policy_module
    from kindly_web_search_mcp_server.settings import settings

    # Pin the settings object the policy module actually reads so ambient
    # settings swapping in other tests cannot leak in.
    monkeypatch.setattr(
        intent_policy_module,
        "settings",
        settings,
    )
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

@pytest.mark.parametrize(
    "intent, expected_ddg_args",
    [
        ("general", {"backend": "duckduckgo,yahoo,yandex,brave"}),
        ("ai_coding_and_infrastructure", {"backend": "duckduckgo,yahoo,yandex,brave"}),
        ("digital_humanities", {"backend": "grokipedia,wikipedia"}),
        ("comparison", {"backend": "duckduckgo,yahoo,yandex,brave"}),
        ("social_media", {"backend": "duckduckgo,yahoo,yandex,brave"}),
        ("news", {"category": "news"}),
    ],
)
def test_ddg_provider_arguments_per_intent(intent, expected_ddg_args):
    policy = resolve_intent_policy(intent)
    assert policy.provider_arguments["ddg"] == expected_ddg_args

@pytest.mark.parametrize(
    "intent, expected_exa_args",
    [
        ("general", {"type": "auto"}),
        ("ai_coding_and_infrastructure", {"type": "auto"}),
        ("digital_humanities", {"type": "auto", "category": "publication"}),
        ("comparison", {"type": "auto"}),
        ("social_media", {"type": "auto", "category": "personal site"}),
        ("news", {"type": "auto", "category": "news", "freshness": "week"}),
    ],
)
def test_exa_provider_arguments_per_intent(intent, expected_exa_args):
    policy = resolve_intent_policy(intent)
    assert policy.provider_arguments["exa"] == expected_exa_args


