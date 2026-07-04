"""Build branch specs for the intent-owned search pipeline."""

from __future__ import annotations

from ..settings import settings
from .branch_executor import SearchBranchSpec
from .intents import SearchIntent
from .normalize import normalize_query
from .provider_plan import ProviderExecutionPlan
from .query_rewrite_models import QueryVariant


def _shard_providers(
    providers: list[str],
    branch_count: int,
) -> list[list[str]]:
    """Shard provider names across branches via round-robin without duplication.

    Each provider appears in exactly one branch. When there are more branches
    than providers some branches will be empty; when there are fewer branches
    than providers each branch gets a distinct subset.
    """
    if not providers or branch_count <= 1:
        return [providers[:]]

    shards: list[list[str]] = [[] for _ in range(branch_count)]
    for i, provider in enumerate(providers):
        shards[i % branch_count].append(provider)
    return shards


def build_search_branch_specs(
    *,
    intent: SearchIntent,
    normalized_query: str,
    rewrite_variants: list[QueryVariant],
    num_results: int,
    active_provider_names: list[str],
    provider_plan: ProviderExecutionPlan,
) -> list[SearchBranchSpec]:
    original_variant = QueryVariant(
        kind="original",
        target="keyword",
        query=normalize_query(normalized_query),
        why="Original query is always preserved.",
        weight=1.0,
        branch_type="original",
        reason="Original query is always preserved.",
        max_results=num_results,
    )
    max_rewrites = max(0, settings.query_rewrite_max_variants)
    planned_variants = [original_variant, *rewrite_variants[:max_rewrites]]

    branch_count = len(planned_variants)
    provider_shards = _shard_providers(list(active_provider_names), branch_count)

    branch_specs: list[SearchBranchSpec] = []
    for index, variant in enumerate(planned_variants):
        branch_providers = (
            provider_shards[index]
            if index < len(provider_shards)
            else list(active_provider_names)
        )
        branch_specs.append(
            SearchBranchSpec(
                index=index,
                intent=intent,
                query=variant.query,
                branch_type=variant.branch_type or variant.kind,
                weight=variant.weight,
                providers=branch_providers or active_provider_names or None,
                provider_options_by_name=provider_plan.options.bundles,
                max_results=variant.max_results or num_results,
                reason=variant.reason or variant.why,
                must_keep_terms=variant.must_keep_terms,
            )
        )
    return branch_specs
