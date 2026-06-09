from __future__ import annotations

from kindly_web_search_mcp_server.search.intents import normalize_intent
from kindly_web_search_mcp_server.search.profiles.resolve import resolve_search_profile


def test_normalize_intent_aliases() -> None:
    assert normalize_intent("code") == "ai_coding"
    assert normalize_intent("general_research") == "general"
    assert normalize_intent("digital_humanities") == "digital_humanities"


def test_resolve_search_profile_inherits_general_defaults() -> None:
    general = resolve_search_profile("general")
    ai_coding = resolve_search_profile("ai_coding")

    assert general.name == "general"
    assert ai_coding.name == "ai_coding"
    assert ai_coding.parent == "general"
    assert ai_coding.provider_weights == general.provider_weights
