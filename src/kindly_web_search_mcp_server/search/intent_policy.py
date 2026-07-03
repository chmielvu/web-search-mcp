"""Intent-owned search policy for provider routing and query controls."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..settings import settings
from .intents import SearchIntent, normalize_intent
from .options import SearchOptions


def _merge_weights(overrides: dict[str, float] | None = None) -> dict[str, float]:
    weights = dict(settings.rrf_provider_weights)
    if overrides:
        weights.update(overrides)
    return weights


@dataclass(frozen=True, slots=True)
class IntentSearchPolicy:
    intent: SearchIntent
    policy_version: str = "1.0"
    specialized_providers: tuple[str, ...] = ()
    provider_weights: dict[str, float] = field(default_factory=dict)
    provider_arguments: dict[str, dict[str, object]] = field(default_factory=dict)
    search_options_overrides: dict[str, object] = field(default_factory=dict)
    rewrite_temperature: float = 0.0

    def apply_search_options(
        self,
        search_options: SearchOptions | None,
    ) -> SearchOptions | None:
        if search_options is None and not self.search_options_overrides:
            return None
        base = search_options or SearchOptions()
        if not self.search_options_overrides:
            return base
        overrides = dict(self.search_options_overrides)
        # User-specified categories take priority over intent defaults.
        if base.searxng_categories:
            overrides.pop("searxng_categories", None)
        return replace(base, **overrides).validate()


# Base shared policy kwargs (no category overrides — each intent sets its own).
_BASE_POLICY_KWARGS = dict(
    provider_weights=_merge_weights(),
    rewrite_temperature=0.0,
)

# SearXNG category → intent mapping:
#   general (11 engines): bing, brave, yacy, encyclosearch, crowdview, wiby,
#     hackernews, reddit, wolframalpha, openmeteo, ddg_definitions, marginalia, mwmbl
#   it (12 engines): github, github code, stackoverflow, askubuntu, superuser,
#     npm, huggingface, repology, docker hub, codeberg, gitlab, pypi
#   science (4 engines): arxiv, semantic scholar, openalex, pubmed
#
# Intent routing:
#   general           → general + it        (broad discovery across web + tech)
#   ai_coding_and_infrastructure → it                  (targeted code/docs/package search)
#   digital_humanities → it + science        (scholarly + tech infrastructure)
#   comparison        → general + it        (broad coverage for comparing options)

_INTENT_POLICIES: dict[SearchIntent, IntentSearchPolicy] = {
    "general": IntentSearchPolicy(
        intent="general",
        search_options_overrides={"searxng_categories": ("general", "it")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "ai_coding_and_infrastructure": IntentSearchPolicy(
        intent="ai_coding_and_infrastructure",
        specialized_providers=("telegram",),
        search_options_overrides={"searxng_categories": ("it",)},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "digital_humanities": IntentSearchPolicy(
        intent="digital_humanities",
        search_options_overrides={"searxng_categories": ("it", "science")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "comparison": IntentSearchPolicy(
        intent="comparison",
        search_options_overrides={"searxng_categories": ("general", "it")},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": True},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "social_media": IntentSearchPolicy(
        intent="social_media",
        specialized_providers=("telegram",),
        search_options_overrides={"searxng_categories": ("general",)},
        provider_arguments={
            "brightdata": {"country": "us", "language": "en", "exact_match": False},
        },
        **_BASE_POLICY_KWARGS,
    ),
    "news": IntentSearchPolicy(
        intent="news",
        specialized_providers=("telegram",),
        search_options_overrides={"searxng_categories": ("news", "general")},
        provider_arguments={
            "brightdata": {"search_type": "news", "language": "en", "use_bing": False},
        },
        **_BASE_POLICY_KWARGS,
    ),
}


def resolve_intent_policy(intent: str | None) -> IntentSearchPolicy:
    return _INTENT_POLICIES[normalize_intent(intent)]
