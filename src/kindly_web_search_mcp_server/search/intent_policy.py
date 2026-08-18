"""Intent-owned search policy for provider routing and query controls."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from .intents import SearchIntent, normalize_intent
from .options import SearchOptions
from ..settings import settings


@dataclass(frozen=True, slots=True)
class IntentSearchPolicy:
    intent: SearchIntent
    policy_version: str = "1.0"
    specialized_providers: tuple[str, ...] = ()
    provider_arguments: dict[str, dict[str, object]] = field(default_factory=dict)
    search_options_overrides: dict[str, object] = field(default_factory=dict)
    rewrite_temperature: float = 0.0
    freshness: str | None = None

    def apply_search_options(self, search_options: SearchOptions | None) -> SearchOptions | None:
        if search_options is None and not self.search_options_overrides:
            return None
        base = search_options or SearchOptions()
        if not self.search_options_overrides:
            return base
        return replace(base, **self.search_options_overrides).validate()


_BASE_POLICY_KWARGS: dict[str, Any] = {"rewrite_temperature": 0.0}

_DEFAULT_INTENT_PROVIDER_SUBSCRIPTIONS: dict[SearchIntent, tuple[str, ...]] = {
    "social_media": ("telegram", "reddit"),
    "ai_coding_and_infrastructure": (
        "telegram",
        "hackernews",
        "github",
        "sourcegraph",
        "gitlab",
        "reddit",
    ),
    "news": ("telegram", "brave_news"),
    "general": (),
    "comparison": (),
    "digital_humanities": (),
}

_INTENT_SUBSCRIPTIONS: dict[SearchIntent, list[str]] = {
    intent: list(providers) for intent, providers in _DEFAULT_INTENT_PROVIDER_SUBSCRIPTIONS.items()
}


def get_subscribed_specialized_providers(intent: SearchIntent) -> tuple[str, ...]:
    """Return tuple of specialized provider names subscribed to the given intent."""
    return tuple(_INTENT_SUBSCRIPTIONS.get(intent, []))


def register_provider_subscription(provider_name: str, intents: Sequence[SearchIntent]) -> None:
    """Register a specialized provider for one or more intents."""
    for intent in intents:
        current = _INTENT_SUBSCRIPTIONS.setdefault(intent, [])
        if provider_name not in current:
            current.append(provider_name)


_INTENT_POLICIES: dict[SearchIntent, IntentSearchPolicy] = {
    "general": IntentSearchPolicy(
        intent="general",
        search_options_overrides={"searxng_categories": ("general", "it")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
            "tavily": {"topic": "general"},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "ai_coding_and_infrastructure": IntentSearchPolicy(
        intent="ai_coding_and_infrastructure",
        search_options_overrides={"searxng_categories": ("it",)},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
            "tavily": {"search_depth": "advanced"},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "digital_humanities": IntentSearchPolicy(
        intent="digital_humanities",
        search_options_overrides={"searxng_categories": ("it", "science")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
            "tavily": {"search_depth": "advanced"},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "comparison": IntentSearchPolicy(
        intent="comparison",
        search_options_overrides={"searxng_categories": ("general", "it")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
            "tavily": {"search_depth": "advanced"},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "social_media": IntentSearchPolicy(
        intent="social_media",
        search_options_overrides={"searxng_categories": ("general",)},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False}
        },
        **_BASE_POLICY_KWARGS,
    ),
    "news": IntentSearchPolicy(
        intent="news",
        policy_version="1.1",
        freshness="week",
        search_options_overrides={"searxng_categories": ("news", "general")},
        provider_arguments={
            "brightdata": {"search_type": "news", "language": "en"},
            "brave_news": {"freshness": "week"},
            "tavily": {"topic": "news", "time_range": "week", "search_depth": "advanced"},
        },
        **_BASE_POLICY_KWARGS,
    ),
}


def resolve_intent_policy(intent: str | None) -> IntentSearchPolicy:
    normalized = normalize_intent(intent)
    base = _INTENT_POLICIES[normalized]
    specialized_providers = get_subscribed_specialized_providers(normalized)
    base = replace(base, specialized_providers=specialized_providers)
    goggles = settings.brave_goggles_by_intent.get(normalized)
    if not goggles:
        return base
    provider_arguments = {name: dict(bundle) for name, bundle in base.provider_arguments.items()}
    for provider_name in ("brave", "brave_news"):
        merged = dict(provider_arguments.get(provider_name, {}))
        merged["goggles"] = list(goggles)
        provider_arguments[provider_name] = merged
    return replace(base, provider_arguments=provider_arguments)
