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

    def test_user_categories_override_do_not_get_replaced(self):
        """When user explicitly passes categories, they should be preserved."""
        policy = resolve_intent_policy("ai_coding_and_infrastructure")
        user_so = SearchOptions(searxng_categories=("science",))
        so = policy.apply_search_options(user_so)
        assert so.searxng_categories == ("science",)
