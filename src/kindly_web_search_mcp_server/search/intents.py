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

INTENT_PROVIDERS: dict[SearchIntent, list[str]] = {
    "general":              ["gemini", "hackernews", "reddit",
                             "github_graphql", "stackexchange"],
    "ai_coding":            ["gemini", "hackernews", "reddit",
                             "github_graphql", "stackexchange"],
    "digital_humanities":   ["gemini", "hackernews", "reddit",
                             "github_graphql", "stackexchange"],
    "comparison":           ["gemini", "hackernews", "reddit",
                             "github_graphql", "stackexchange"],
}


def normalize_intent(value: str | None) -> SearchIntent:
    if not value:
        return "general"
    key = value.strip().casefold()
    return INTENT_ALIASES.get(key, "general")
