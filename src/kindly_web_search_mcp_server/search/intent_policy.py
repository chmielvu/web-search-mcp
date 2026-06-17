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
        return replace(base, **self.search_options_overrides).validate()


_INTENT_POLICIES: dict[SearchIntent, IntentSearchPolicy] = {
    "general": IntentSearchPolicy(
        intent="general",
        specialized_providers=("gemini", "hackernews", "reddit"),
        provider_weights=_merge_weights(),
        provider_arguments={},
        search_options_overrides={},
        rewrite_temperature=0.35,
    ),
    "ai_coding": IntentSearchPolicy(
        intent="ai_coding",
        specialized_providers=("github_graphql", "gemini", "grok_openrouter", "jina"),
        provider_weights=_merge_weights(
            {
                "github_graphql": 1.2,
                "gemini": 1.1,
                "grok_openrouter": 1.05,
                "jina": 1.05,
            }
        ),
        provider_arguments={},
        search_options_overrides={},
        rewrite_temperature=0.15,
    ),
    "digital_humanities": IntentSearchPolicy(
        intent="digital_humanities",
        specialized_providers=("hackernews", "reddit", "tavily"),
        provider_weights=_merge_weights(
            {
                "hackernews": 1.15,
                "reddit": 1.1,
                "tavily": 1.05,
            }
        ),
        provider_arguments={},
        search_options_overrides={},
        rewrite_temperature=0.25,
    ),
    "comparison": IntentSearchPolicy(
        intent="comparison",
        specialized_providers=("gemini", "github_graphql", "grok_openrouter", "reddit"),
        provider_weights=_merge_weights(
            {
                "gemini": 1.15,
                "github_graphql": 1.1,
                "grok_openrouter": 1.05,
                "reddit": 1.05,
            }
        ),
        provider_arguments={},
        search_options_overrides={},
        rewrite_temperature=0.2,
    ),
}


def resolve_intent_policy(intent: str | None) -> IntentSearchPolicy:
    return _INTENT_POLICIES[normalize_intent(intent)]
