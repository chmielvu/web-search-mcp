"""Resolved provider execution plan for a search request."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .intents import SearchIntent
from .intent_policy import resolve_intent_policy
from .options import SearchOptions
from .provider_config import (
    ProviderGroup,
    get_provider_configs,
    resolve_provider_configs,
    select_paid_serp_configs,
)
from .provider_options import ProviderOptionBundle, ProviderOptionSet


@dataclass(frozen=True, slots=True)
class ProviderExecutionPlan:
    intent: SearchIntent
    policy_version: str
    provider_names: tuple[str, ...]
    provider_weights: dict[str, float]
    search_options: SearchOptions | None
    options: ProviderOptionSet
    plan_version: str = "1.0"


def _merge_provider_names(*groups: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    names: list[str] = []
    for group in groups:
        for name in group:
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return tuple(names)


def _build_bundles(
    *,
    provider_names: tuple[str, ...],
    provider_weights: dict[str, float],
    provider_arguments: dict[str, dict[str, object]],
    search_options: SearchOptions | None,
) -> ProviderOptionSet:
    bundles = {
        name: ProviderOptionBundle(
            provider_name=name,
            search_options=search_options,
            weight=provider_weights.get(name, 1.0),
            arguments=dict(provider_arguments.get(name, {})),
        )
        for name in provider_names
    }
    return ProviderOptionSet(bundles=bundles)


def build_provider_execution_plan(
    *,
    intent: SearchIntent = "general",
    public_options: SearchOptions | None,
) -> ProviderExecutionPlan:
    policy = resolve_intent_policy(intent)
    registry = get_provider_configs()
    free_names = [
        name
        for name, config in registry.items()
        if config.group == ProviderGroup.free
    ]
    paid_names = [
        name
        for name, config in registry.items()
        if config.group == ProviderGroup.paid_serp
    ]

    free_configs = resolve_provider_configs(free_names)
    brightdata_name = "brightdata"
    other_paid_names = [n for n in paid_names if n != brightdata_name]
    other_paid_configs = resolve_provider_configs(other_paid_names)
    selected_paid = select_paid_serp_configs(other_paid_configs, limit=1)
    brightdata_config = resolve_provider_configs([brightdata_name])
    if brightdata_config:
        selected_paid = [brightdata_config[0]] + selected_paid
    specialized_configs = resolve_provider_configs(policy.specialized_providers)

    provider_names = _merge_provider_names(
        [config.name for config in free_configs],
        [config.name for config in selected_paid],
        [config.name for config in specialized_configs],
    )
    provider_weights = dict(policy.provider_weights)
    effective_options = policy.apply_search_options(public_options)
    options = _build_bundles(
        provider_names=provider_names,
        provider_weights=provider_weights,
        provider_arguments=policy.provider_arguments,
        search_options=effective_options,
    )
    return ProviderExecutionPlan(
        intent=policy.intent,
        policy_version=policy.policy_version,
        provider_names=provider_names,
        provider_weights=provider_weights,
        search_options=effective_options,
        options=options,
    )


def build_cache_identity(
    *,
    query: str,
    intent: SearchIntent,
    provider_plan: ProviderExecutionPlan,
    search_options: SearchOptions | None,
    rewrite_enabled: bool,
) -> str:
    payload = "|".join(
        [
            query,
            intent,
            provider_plan.policy_version,
            provider_plan.plan_version,
            ",".join(provider_plan.provider_names),
            str(search_options.cache_fingerprint() if search_options else ""),
            str(int(rewrite_enabled)),
        ]
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:24]
