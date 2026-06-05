"""Web search orchestrator: coordinate rewrite → multi-provider search → merge → rerank.

Simplified: bypass (preserve literals) or expand (LLM rewrite).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..models import WebSearchResponse
from ..settings import settings
from ..telemetry import record_domain_diversity
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from ..cache.result_memory import get_result_memory_store  # noqa: F401
from ..embeddings import embed_query  # noqa: F401
from ..search_instrumented import search_single_query
from .branch_executor import (
    SearchBranchSpec,
    execute_search_branches,
    select_providers_for_variant,
)
from .finalize_results import build_search_response, maybe_extract_entities
from .flow_observability import emit_result_lists_summary, serialize_query_variants
from .options import SearchOptions
from .merge import merge_search_results
from .normalize import normalize_query
from .provider_config import resolve_providers_for_search
from .query_policy import RewriteMode, RewritePolicy, classify_search_query
from ..entity.models import EntitySpan  # for type in sig (optional dep ok)
from .result_memory_pipeline import (
    inject_result_memory_candidates,
    store_result_memory_results,
)
from .query_rewrite import rewrite_search_query
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


async def run_web_search(
    query: str,
    *,
    num_results: int,
    rewrite: bool = True,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    research_goal: str | None = None,
    search_options: SearchOptions | None = None,
    query_entities: list[EntitySpan] | None = None,
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
        query_entities: Pre-extracted entities from the original (raw) query only.
            Used to augment must-keep terms in rewrite policy. Extraction happens
            exactly once in the web_search server entrypoint.

    Returns:
        WebSearchResponse with merged and reranked results
    """
    normalized_query = normalize_query(query)
    requested_count = _resolve_requested_result_count(
        num_results, search_options.result_offset if search_options else 0
    )
    # If no rewrite path, still let entities influence the (bypass) policy
    if not rewrite and query_entities:
        rewrite_policy = classify_search_query(normalized_query, entities=query_entities)
    else:
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
                    entities=query_entities,
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
        branch_specs: list[SearchBranchSpec] = []
        if rewrite_plan:
            for index, variant in enumerate(rewrite_plan.variants):
                variant_providers = select_providers_for_variant(
                    variant, active_provider_names
                )
                if variant_providers is not None and not variant_providers:
                    continue
                branch_specs.append(
                    SearchBranchSpec(
                        index=index,
                        query=variant.query,
                        branch_type=variant.branch_type or variant.kind,
                        weight=variant.weight,
                        providers=variant_providers,
                        max_results=variant.max_results or per_query_k,
                        reason=variant.reason or variant.why,
                        must_keep_terms=variant.must_keep_terms,
                    )
                )
        else:
            branch_specs = [
                SearchBranchSpec(
                    index=0,
                    query=normalized_query,
                    branch_type="original",
                    weight=1.0,
                    providers=providers,
                    max_results=per_query_k,
                    reason="Original query preserved.",
                )
            ]

        branch_batch = await execute_search_branches(
            branch_specs,
            http_client=client,
            diagnostics=diagnostics,
            search_options=search_options,
            search_runner=search_single_query,
            max_concurrency=settings.query_decomposition_max_concurrency,
        )
        result_lists = branch_batch.result_lists
        branch_queries = branch_batch.branch_queries
        branch_providers = branch_batch.branch_providers
        list_weights = branch_batch.list_weights

    emit_result_lists_summary(
        logger,
        "search.orchestrator.branches",
        query=query,
        result_lists=result_lists,
        branch_queries=branch_queries,
        branch_providers=branch_providers,
        list_weights=list_weights,
        branch_metadata=branch_batch.branch_metadata,
    )

    # Phase 7.2: Result memory candidate injection (before RRF)
    # Convert memory hits to virtual provider list; weight from settings; dedup happens in merge.
    (
        result_lists,
        list_weights,
        query_vec_for_mem,
        memory_injected,
    ) = await inject_result_memory_candidates(
        query=query,
        normalized_query=normalized_query,
        result_lists=result_lists,
        list_weights=list_weights,
        get_result_memory_store_fn=get_result_memory_store,
        embed_query_fn=embed_query,
    )

    merged = merge_search_results(
        result_lists,
        list_weights=list_weights,
    )

    # Record domain diversity for homogeneous result detection
    unique_domains = len(set(r.domain for r in merged if r.domain))
    record_domain_diversity(unique_domains, len(merged), providers or [])

    if settings.reranking_enabled and len(merged) > 1:
        try:
            query_type_hint = (
                rewrite_plan.classifier.intent
                if rewrite_plan and rewrite_plan.classifier
                else None
            )
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
                research_goal=research_goal,
                query_type_hint=query_type_hint,
            )
        except Exception as exc:
            logger.warning("Reranking failed in web search orchestrator: %s", exc)

    # Phase 7.2: store current (post-rerank) results to memory + compare injected survivors
    await store_result_memory_results(
        normalized_query=normalized_query,
        results=merged,
        query_embedding=query_vec_for_mem,
        get_result_memory_store_fn=get_result_memory_store,
        embed_query_fn=embed_query,
    )

    # compare injected vs final post-rerank for survival signal
    if memory_injected:
        injected_urls = {result.link for result in memory_injected}
        survived = [r for r in merged if r.link in injected_urls]
        if survived:
            emit_observability_event(
                logger,
                "result_memory.candidate_survived",
                query=query,
                injected_count=len(memory_injected),
                survived_count=len(survived),
                survived_urls=[r.link for r in survived][:5],
            )

    result_offset = search_options.result_offset if search_options else 0
    final_results = merged[result_offset : result_offset + num_results]
    candidate_count = len(merged)
    has_more = result_offset + len(final_results) < candidate_count
    next_offset = result_offset + len(final_results) if has_more else None

    final_results = await maybe_extract_entities(query=query, results=final_results)

    (
        provider_warnings,
        providers_used,
        response,
    ) = build_search_response(
        query=query,
        normalized_query=normalized_query,
        research_goal=research_goal,
        rewrite=rewrite,
        rewrite_policy=rewrite_policy,
        unique_domains=unique_domains,
        merged=merged,
        final_results=final_results,
        providers=providers,
        result_offset=result_offset,
        candidate_count=candidate_count,
        has_more=has_more,
        next_offset=next_offset,
    )

    return response
