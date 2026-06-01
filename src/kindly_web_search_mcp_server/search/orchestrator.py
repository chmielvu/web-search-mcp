"""Web search orchestrator: coordinate rewrite → multi-provider search → merge → rerank.

Simplified: bypass (preserve literals) or expand (LLM rewrite).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..models import ProviderWarning, WebSearchResponse
from ..settings import settings
from ..telemetry import record_domain_diversity
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from ..search_instrumented import search_single_query
from .flow_observability import emit_result_lists_summary, serialize_query_variants
from .options import SearchOptions
from .merge import merge_search_results
from .normalize import normalize_query
from .provider_config import diagnose_providers, resolve_providers_for_search
from .query_policy import RewriteMode, RewritePolicy
from .query_rewrite import rewrite_search_query
from .query_rewrite_models import (
    COMMUNITY_PROVIDER_NAMES,
    KEYWORD_PROVIDER_NAMES,
    NEURAL_PROVIDER_NAMES,
    QueryVariant,
)

logger = logging.getLogger(__name__)
_rerank_results: Any = None


def _resolve_per_query_k(num_results: int, mode: RewriteMode) -> int:
    """Determine how many results to fetch per query based on mode.

    bypass: 2x (preserve precision, minimal expansion)
    expand: 3x (multiple variants need more results for merge)
    """
    if mode == "bypass":
        return max(num_results * 2, 6)
    # expand mode
    return max(num_results * 3, 9)


def _resolve_requested_result_count(num_results: int, result_offset: int) -> int:
    return max(1, num_results + max(0, result_offset))


def _select_providers_for_variant(
    variant: QueryVariant,
    active_provider_names: list[str],
) -> list[str] | None:
    if variant.target == "all":
        return active_provider_names or None
    if variant.target == "keyword":
        selected = [
            name for name in active_provider_names if name in KEYWORD_PROVIDER_NAMES
        ]
        return selected if selected else None
    if variant.target == "community":
        selected = [
            name for name in active_provider_names if name in COMMUNITY_PROVIDER_NAMES
        ]
        return selected if selected else None
    selected = [name for name in active_provider_names if name in NEURAL_PROVIDER_NAMES]
    return selected if selected else None


async def run_web_search(
    query: str,
    *,
    num_results: int,
    rewrite: bool = True,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    research_goal: str | None = None,
    search_options: SearchOptions | None = None,
) -> WebSearchResponse:
    """Execute web search with optional query rewriting.

    Flow:
    1. Rewrite query (if enabled) → get final_queries
    2. Search each query in parallel via configured providers
    3. Merge results via RRF
    4. Rerank top results

    Args:
        query: Raw query string
        num_results: Number of final results to return
        rewrite: Whether to enable query rewriting
        diagnostics: Optional diagnostics emitter
        providers: Optional list of specific providers to use
        research_goal: Optional context/goal from client to guide query optimization

    Returns:
        WebSearchResponse with merged and reranked results
    """
    normalized_query = normalize_query(query)
    requested_count = _resolve_requested_result_count(
        num_results, search_options.result_offset if search_options else 0
    )
    rewrite_policy = RewritePolicy(mode="bypass", reason="Rewrite disabled by caller.")
    active_provider_names = [
        config.name for config in resolve_providers_for_search(providers)
    ]

    if rewrite:
        try:
            rewrite_plan = await asyncio.wait_for(
                rewrite_search_query(
                    normalized_query,
                    diagnostics=diagnostics,
                    research_goal=research_goal,
                    providers=providers,
                ),
                timeout=15.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "Query rewrite failed (will proceed with original query): %s", exc
            )
            rewrite_plan = None
            rewrite_policy = RewritePolicy(
                mode="bypass",
                reason=f"Rewrite fallback: {type(exc).__name__}",
            )
            queries = [normalized_query]
        else:
            queries = rewrite_plan.final_queries
            rewrite_policy = rewrite_plan.policy
    else:
        queries = [normalized_query]
        rewrite_plan = None

    per_query_k = _resolve_per_query_k(requested_count, rewrite_policy.mode)

    emit_observability_event(
        logger,
        "search.orchestrator.plan",
        query=query,
        normalized_query=normalized_query,
        rewrite_enabled=rewrite,
        rewrite_policy=rewrite_policy.mode,
        rewrite_reason=rewrite_policy.reason,
        final_queries=queries,
        query_variants=serialize_query_variants(rewrite_plan.variants)
        if rewrite_plan
        else [],
        active_providers=active_provider_names,
        per_query_k=per_query_k,
        providers_requested=providers or [],
        research_goal=research_goal,
        search_options=search_options.to_dict() if search_options else None,
    )

    if diagnostics:
        diagnostics.emit(
            "web_search.rewrite_plan",
            "Resolved search queries",
            {
                "query": normalized_query,
                "queries": queries,
                "rewrite": rewrite,
                "policy": rewrite_policy.mode,
                "per_query_k": per_query_k,
                "search_options": search_options.to_dict() if search_options else None,
            },
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5, read=20, write=20, pool=20),
        follow_redirects=True,
    ) as client:
        if rewrite_plan:
            search_tasks = []
            list_weights: list[float] = []
            branch_queries: list[str] = []
            branch_providers: list[list[str] | None] = []
            for variant in rewrite_plan.variants:
                variant_providers = _select_providers_for_variant(
                    variant, active_provider_names
                )
                if variant_providers is not None and not variant_providers:
                    continue
                list_weights.append(variant.weight)
                branch_queries.append(variant.query)
                branch_providers.append(variant_providers)
                search_tasks.append(
                    search_single_query(
                        variant.query,
                        num_results=per_query_k,
                        http_client=client,
                        diagnostics=diagnostics,
                        providers=variant_providers,
                        search_options=search_options,
                    )
                )
            result_lists = await asyncio.gather(*search_tasks) if search_tasks else []
        else:
            branch_queries = [normalized_query]
            branch_providers = [providers]
            result_lists = await asyncio.gather(
                *[
                    search_single_query(
                        normalized_query,
                        num_results=per_query_k,
                        http_client=client,
                        diagnostics=diagnostics,
                        providers=providers,
                        search_options=search_options,
                    )
                ]
            )

            list_weights = [1.0] * len(result_lists)

    emit_result_lists_summary(
        logger,
        "search.orchestrator.branches",
        query=query,
        result_lists=result_lists,
        branch_queries=branch_queries,
        branch_providers=branch_providers,
        list_weights=list_weights,
    )

    merged = merge_search_results(
        result_lists,
        list_weights=list_weights if rewrite_plan else None,
    )

    # Record domain diversity for homogeneous result detection
    unique_domains = len(set(r.domain for r in merged if r.domain))
    record_domain_diversity(unique_domains, len(merged), providers or [])

    if settings.reranking_enabled and len(merged) > 1:
        try:
            global _rerank_results
            if _rerank_results is None:
                from ..rerank import rerank_results as _loaded_rerank_results

                _rerank_results = _loaded_rerank_results
            merged = await _rerank_results(
                normalized_query,
                merged,
                top_k=requested_count,
                searxng_time_range=search_options.searxng_time_range
                if search_options
                else None,
            )
        except Exception as exc:
            logger.warning("Reranking failed in web search orchestrator: %s", exc)

    result_offset = search_options.result_offset if search_options else 0
    final_results = merged[result_offset : result_offset + num_results]
    candidate_count = len(merged)
    has_more = result_offset + len(final_results) < candidate_count
    next_offset = result_offset + len(final_results) if has_more else None

    # Aggregate providers_used from merged results
    providers_used = sorted(set(p for r in final_results for p in (r.providers or [])))

    # Build provider warnings for explicitly requested providers that couldn't fire
    provider_diagnoses = diagnose_providers(providers)
    provider_warnings = [
        ProviderWarning(provider=d.name, error=d.reason, error_type="unavailable")
        for d in provider_diagnoses
        if not d.available
    ]

    emit_observability_event(
        logger,
        "search.orchestrator.response",
        query=query,
        research_goal=research_goal,
        normalized_query=normalized_query,
        rewrite_enabled=rewrite,
        rewrite_policy=rewrite_policy.mode,
        rewrite_reason=rewrite_policy.reason,
        unique_domains=unique_domains,
        merged_result_count=len(merged),
        final_result_count=len(final_results),
        providers_requested=providers or [],
        providers_used=providers_used,
        warnings=[warning.model_dump() for warning in provider_warnings],
        results=final_results,
        merged_results=merged,
        result_window={
            "offset": result_offset,
            "returned": len(final_results),
            "candidate_count": candidate_count,
            "has_more": has_more,
            "next_offset": next_offset,
        },
    )

    return WebSearchResponse(
        query=query,
        results=final_results,
        total_results=len(final_results),
        result_window={
            "offset": result_offset,
            "returned": len(final_results),
            "candidate_count": candidate_count,
            "has_more": has_more,
            "next_offset": next_offset,
        },
        providers_used=providers_used,
        warnings=provider_warnings or None,
    )
