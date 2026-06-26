"""Canonical search intents for the 0.2 control plane."""

from __future__ import annotations

from typing import Literal

SearchIntent = Literal[
    "general",
    "ai_coding_and_infrastructure",
    "digital_humanities",
    "comparison",
    "social_media",
    "news",
]

INTENT_ALIASES: dict[str, SearchIntent] = {
    "code": "ai_coding_and_infrastructure",
    "ai_coding": "ai_coding_and_infrastructure",
    "ai_coding_and_infrastructure": "ai_coding_and_infrastructure",
    "general_research": "general",
    "comparison": "comparison",
    "general": "general",
    "digital_humanities": "digital_humanities",
    "social_media": "social_media",
    "news": "news",
}


def normalize_intent(value: str | None) -> SearchIntent:
    if not value:
        return "general"
    key = value.strip().casefold()
    return INTENT_ALIASES.get(key, "general")
