"""Resolved provider execution plan for a search request."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .intents import SearchIntent
from .options import SearchOptions
from .profiles.models import SearchProfile
from .provider_options import ProviderOptionBundle, ProviderOptionSet
from .provider_config import resolve_providers_for_search


@dataclass(frozen=True, slots=True)
class ProviderExecutionPlan:
    provider_names: tuple[str, ...]
    provider_weights: dict[str, float]
    options: ProviderOptionSet
    plan_version: str = "0.2"


def build_provider_execution_plan(
    *,
    profile: SearchProfile,
    intent: SearchIntent = "general",
    public_options: SearchOptions | None,
) -> ProviderExecutionPlan:
    provider_names = tuple(profile.provider_names or ())
    if not provider_names:
        provider_names = tuple(
            config.name for config in resolve_providers_for_search(intent)
        )
    if not provider_names:
        provider_names = tuple(profile.provider_weights.keys()) or (
            "searxng",
            "brave",
        )
    provider_weights = dict(profile.provider_weights)
    bundles = {
        name: ProviderOptionBundle(
            provider_name=name,
            search_options=public_options,
            weight=provider_weights.get(name, 1.0),
            arguments=dict(profile.provider_arguments.get(name, {})),
        )
        for name in provider_names
    }
    return ProviderExecutionPlan(
        provider_names=provider_names,
        provider_weights=provider_weights,
        options=ProviderOptionSet(bundles=bundles),
    )


def build_cache_identity(
    *,
    query: str,
    profile: SearchProfile,
    provider_plan: ProviderExecutionPlan,
    search_options: SearchOptions | None,
    rewrite_enabled: bool,
) -> str:
    payload = "|".join(
        [
            query,
            profile.name,
            provider_plan.plan_version,
            ",".join(provider_plan.provider_names),
            str(search_options.cache_fingerprint() if search_options else ""),
            str(int(rewrite_enabled)),
        ]
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:24]
