"""Resolved provider execution plan for a search request."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .intents import SearchIntent
from .options import SearchOptions
from .profiles.models import SearchProfile
from .provider_options import ProviderOptionBundle, ProviderOptionSet
from .provider_config import (
    ProviderGroup,
    resolve_providers_for_search,
    resolve_provider_configs,
    select_serp_paid_configs,
)


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
    provider_configs = (
        list(resolve_provider_configs(profile.provider_names or (), intent=intent))
        if profile.provider_names
        else []
    )
    if not provider_configs:
        provider_configs = resolve_providers_for_search(intent)
    provider_names_list: list[str] = []
    if provider_configs:
        selected_serp_paid = select_serp_paid_configs(provider_configs)
        inserted_paid = False
        for config in provider_configs:
            if config.group == ProviderGroup.serp_paid:
                if not inserted_paid:
                    provider_names_list.extend(
                        config.name for config in selected_serp_paid
                    )
                    inserted_paid = True
                continue
            provider_names_list.append(config.name)
    if not provider_configs:
        provider_names_list = list(profile.provider_weights.keys()) or [
            "searxng",
            "brave",
        ]
    provider_names = tuple(provider_names_list)
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
