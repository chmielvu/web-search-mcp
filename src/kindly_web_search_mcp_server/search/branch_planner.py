"""Build branch specs for the intent-owned search pipeline."""

from __future__ import annotations

from .branch_executor import SearchBranchSpec
from .intents import SearchIntent
from .normalize import normalize_query
from .provider_plan import ProviderExecutionPlan
from .query_rewrite_models import QueryVariant


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
    planned_variants = [original_variant, *rewrite_variants[:2]]

    branch_specs: list[SearchBranchSpec] = []
    for index, variant in enumerate(planned_variants):
        branch_providers = active_provider_names
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
