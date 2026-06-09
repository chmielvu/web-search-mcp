"""Canonical search intents for the 0.2 control plane."""

from __future__ import annotations

from typing import Literal

SearchIntent = Literal["general", "ai_coding", "digital_humanities", "comparison"]

INTENT_ALIASES: dict[str, SearchIntent] = {
    "code": "ai_coding",
    "general_research": "general",
    "comparison": "comparison",
    "general": "general",
    "ai_coding": "ai_coding",
    "digital_humanities": "digital_humanities",
}


def normalize_intent(value: str | None) -> SearchIntent:
    if not value:
        return "general"
    key = value.strip().casefold()
    return INTENT_ALIASES.get(key, "general")
