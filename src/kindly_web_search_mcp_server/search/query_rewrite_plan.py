from __future__ import annotations

from .normalize import normalize_query
from .provider_config import resolve_providers_for_search
from .query_policy import RewritePolicy
from .query_rewrite_models import (
    COMMUNITY_PROVIDER_NAMES,
    KEYWORD_PROVIDER_NAMES,
    NEURAL_PROVIDER_NAMES,
    ClassifierOutput,
    QueryDecompositionOutput,
    QueryRewritePlan,
    QueryVariant,
    RewriteIntent,
)
from .query_rewrite_validate import dedupe_keep_order, inject_missing_terms


def build_fallback_plan(query: str, policy: RewritePolicy, reason: str) -> QueryRewritePlan:
    cleaned = normalize_query(query)
    original = QueryVariant(
        kind="original",
        target="all",
        query=cleaned,
        why=reason,
        weight=1.0,
    )
    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=[original],
        final_queries=[cleaned],
    )


def active_target_flags(
    providers: list[str] | None,
) -> tuple[bool, bool, bool, list[str]]:
    active_provider_names = [
        config.name for config in resolve_providers_for_search(providers)
    ]
    has_keyword = any(name in KEYWORD_PROVIDER_NAMES for name in active_provider_names)
    has_neural = any(name in NEURAL_PROVIDER_NAMES for name in active_provider_names)
    has_community = any(
        name in COMMUNITY_PROVIDER_NAMES for name in active_provider_names
    )
    return has_keyword, has_neural, has_community, active_provider_names


def build_rewrite_plan(
    *,
    query: str,
    policy: RewritePolicy,
    intent: RewriteIntent,
    keyword_variants: list[QueryVariant],
    neural_variants: list[QueryVariant],
    community_variants: list[QueryVariant] | None = None,
    subquestion_variants: list[QueryVariant] | None = None,
    include_keyword: bool,
    include_neural: bool,
    include_community: bool = False,
    classifier: ClassifierOutput | None = None,
    decomposition: QueryDecompositionOutput | None = None,
    max_variants: int = 3,
) -> QueryRewritePlan:
    cleaned = normalize_query(query)
    variants: list[QueryVariant] = []
    if include_keyword:
        variants.append(
            QueryVariant(
                kind="original",
                target="keyword",
                query=cleaned,
                why="Original query preserved as a keyword search candidate.",
                weight=1.15 if intent == "code" else 1.0,
            )
        )
        keyword_limit = (
            max_variants
            if not include_neural and not include_community and not subquestion_variants
            else max(1, min(2, max_variants - 1))
        )
        for variant in keyword_variants:
            if len(variants) >= keyword_limit:
                break
            variants.append(
                variant.model_copy(
                    update={
                        "query": inject_missing_terms(
                            variant.query, policy.must_keep_terms
                        )
                    }
                )
            )
    if include_neural and len(variants) < max_variants:
        variants.extend(
            variant.model_copy(
                update={
                    "query": inject_missing_terms(variant.query, policy.must_keep_terms)
                }
            )
            for variant in neural_variants[:1]
        )
    if include_community and community_variants and len(variants) < max_variants:
        for variant in community_variants:
            if len(variants) >= max_variants:
                break
            variants.append(
                variant.model_copy(
                    update={
                        "query": inject_missing_terms(
                            variant.query, policy.must_keep_terms
                        )
                    }
                )
            )
    if subquestion_variants and len(variants) < max_variants + len(subquestion_variants):
        for variant in subquestion_variants:
            variants.append(
                variant.model_copy(
                    update={
                        "query": inject_missing_terms(
                            variant.query, policy.must_keep_terms
                        )
                    }
                )
            )

    if not variants:
        return build_fallback_plan(query, policy, "Rewrite produced no usable variants.")

    final_queries = dedupe_keep_order([variant.query for variant in variants])
    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=variants,
        final_queries=final_queries,
        classifier=classifier,
        decomposition=decomposition,
    )
