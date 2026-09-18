"""Canonical search intents and intent-owned search policy."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from ..settings import settings

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


@dataclass(frozen=True, slots=True)
class IntentSearchPolicy:
    """Intent-owned provider arguments and policy version for the planner."""

    intent: SearchIntent
    policy_version: str = "1.0"
    provider_arguments: dict[str, dict[str, object]] = field(default_factory=dict)


_INTENT_POLICIES: dict[SearchIntent, IntentSearchPolicy] = {
    "general": IntentSearchPolicy(
        intent="general",
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
            "tavily": {"topic": "general"},
            "ddg": {"backend": "duckduckgo,yahoo,yandex,brave"},
            "exa": {"type": "auto"},
        },
    ),
    "ai_coding_and_infrastructure": IntentSearchPolicy(
        intent="ai_coding_and_infrastructure",
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
            "tavily": {"search_depth": "advanced"},
            "ddg": {"backend": "duckduckgo,yahoo,yandex,brave"},
            "exa": {"type": "auto"},
        },
    ),
    "digital_humanities": IntentSearchPolicy(
        intent="digital_humanities",
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
            "tavily": {"search_depth": "advanced"},
            "ddg": {"backend": "grokipedia,wikipedia"},
            "exa": {"type": "auto", "category": "publication"},
        },
    ),
    "comparison": IntentSearchPolicy(
        intent="comparison",
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
            "tavily": {"search_depth": "advanced"},
            "ddg": {"backend": "duckduckgo,yahoo,yandex,brave"},
            "exa": {"type": "auto"},
        },
    ),
    "social_media": IntentSearchPolicy(
        intent="social_media",
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False, "mobile": True},
            "ddg": {"backend": "duckduckgo,yahoo,yandex,brave"},
            "exa": {"type": "auto", "category": "personal site"},
        },
    ),
    "news": IntentSearchPolicy(
        intent="news",
        policy_version="1.1",
        provider_arguments={
            "brightdata": {"search_type": "news", "language": "en", "freshness": "week"},
            "brave_news": {"freshness": "week"},
            "tavily": {"topic": "news", "time_range": "week", "search_depth": "advanced"},
            "ddg": {"category": "news"},
            "exa": {"type": "auto", "category": "news", "freshness": "week"},
        },
    ),
}


def resolve_intent_policy(intent: str | None) -> IntentSearchPolicy:
    normalized = normalize_intent(intent)
    base = _INTENT_POLICIES[normalized]
    goggles = settings.brave_goggles_by_intent.get(normalized)
    if not goggles:
        return base
    provider_arguments = {name: dict(bundle) for name, bundle in base.provider_arguments.items()}
    for provider_name in ("brave",):
        merged = dict(provider_arguments.get(provider_name, {}))
        merged["goggles"] = list(goggles)
        provider_arguments[provider_name] = merged
    return replace(base, provider_arguments=provider_arguments)
