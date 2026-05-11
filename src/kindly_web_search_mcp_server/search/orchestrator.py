"""Web search orchestrator: coordinate rewrite → multi-provider search → merge → rerank.

Simplified: bypass (preserve literals) or expand (LLM rewrite).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..models import WebSearchResponse
from ..rerank import rerank_results
from ..settings import settings
from ..telemetry import record_domain_diversity
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event, serialize_search_results
from ..search_instrumented import search_single_query
from .merge import merge_search_results
from .normalize import normalize_query
from .query_policy import RewriteMode, RewritePolicy
from .query_rewrite import rewrite_search_query

logger = logging.getLogger(__name__)


def _resolve_per_query_k(num_results: int, mode: RewriteMode) -> int:
    """Determine how many results to fetch per query based on mode.

    bypass: 2x (preserve precision, minimal expansion)
    expand: 3x (multiple variants need more results for merge)
    """
    if mode == "bypass":
        return max(num_results * 2, 6)
    # expand mode
    return max(num_results * 3, 9)


async def run_web_search(
    query: str,
    *,
    num_results: int,
    rewrite: bool = True,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    research_goal: str | None = None,
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
    rewrite_policy = RewritePolicy(mode="bypass", reason="Rewrite disabled by caller.")

    if rewrite:
        rewrite_plan = await rewrite_search_query(
            normalized_query, diagnostics=diagnostics, research_goal=research_goal
        )
        queries = rewrite_plan.final_queries
        rewrite_policy = rewrite_plan.policy
    else:
        queries = [normalized_query]

    per_query_k = _resolve_per_query_k(num_results, rewrite_policy.mode)

    emit_observability_event(
        logger,
        "search.orchestrator.plan",
        query=query,
        normalized_query=normalized_query,
        rewrite_enabled=rewrite,
        rewrite_policy=rewrite_policy.mode,
        final_queries=queries,
        per_query_k=per_query_k,
        providers_requested=providers or [],
        research_goal=research_goal,
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
            },
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5, read=20, write=20, pool=20),
        follow_redirects=True,
    ) as client:
        result_lists = await asyncio.gather(
            *[
                search_single_query(
                    q,
                    num_results=per_query_k,
                    http_client=client,
                    diagnostics=diagnostics,
                    providers=providers,
                )
                for q in queries
            ]
        )

    merged = merge_search_results(result_lists)

    # Record domain diversity for homogeneous result detection
    unique_domains = len(set(r.domain for r in merged if r.domain))
    record_domain_diversity(unique_domains, len(merged), providers or [])

    if settings.reranking_enabled and len(merged) > 1:
        try:
            merged = await rerank_results(normalized_query, merged, top_k=num_results)
        except Exception as exc:
            logger.warning("Reranking failed in web search orchestrator: %s", exc)

    final_results = merged[:num_results]

    # Aggregate providers_used from merged results
    providers_used = sorted(
        set(p for r in final_results for p in (r.providers or []))
    )

    emit_observability_event(
        logger,
        "search.orchestrator.response",
        query=query,
        normalized_query=normalized_query,
        rewrite_enabled=rewrite,
        rewrite_policy=rewrite_policy.mode,
        unique_domains=unique_domains,
        merged_result_count=len(merged),
        final_result_count=len(final_results),
        providers_requested=providers or [],
        providers_used=providers_used,
        results=serialize_search_results(final_results, max_results=num_results),
    )

    return WebSearchResponse(
        query=query,
        results=final_results,
        total_results=len(final_results),
        providers_used=providers_used,
    )
