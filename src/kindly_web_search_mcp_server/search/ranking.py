"""Core response ranking and finalization."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Sequence
import logging
import time

from ..models import ProviderWarning, WebSearchResponse, WebSearchResult
from ..rerank.bm25 import score_candidates_async
from ..rerank.core import rerank_results
from ..settings import settings
from ..telemetry.spans import get_tracer
from .blocklist import filter_blocked_results
from .contracts import BranchOutcome, SearchRun
from .merge import _memoize_canonicalize, reciprocal_rank_fusion
from .normalize import canonicalize_url

logger = logging.getLogger(__name__)


def _candidate_text(result: WebSearchResult) -> str:
    return f"{result.title}\n{result.snippet}"[:4000]


def _stable_warnings(outcomes: tuple[BranchOutcome, ...]) -> list[ProviderWarning]:
    seen: set[tuple[str, str | None, str]] = set()
    warnings: list[ProviderWarning] = []
    for outcome in outcomes:
        for warning in outcome.warnings:
            key = (warning.provider, warning.error_type, warning.error)
            if key not in seen:
                seen.add(key)
                warnings.append(warning)
    return warnings


async def rank_and_finalize(
    run: SearchRun,
    outcomes: tuple[BranchOutcome, ...],
    *,
    embedding_task: Awaitable[Sequence[float]] | None,
) -> WebSearchResponse:
    tracer = get_tracer()
    rank_started = time.monotonic()
    dc = run.diagnostics
    with tracer.start_as_current_span("search.rank") as span:
        provider_result_lists = []
        for outcome in outcomes:
            for prr in outcome.provider_ranked_results:
                filtered = filter_blocked_results(list(prr.results))
                if filtered:
                    provider_result_lists.append(filtered)

        merged: list[WebSearchResult] = []
        rrf_k = settings.rrf_k
        bm25_scores: list[float] = []
        first_stage_url_scores: dict[str, float] = {}
        second_stage_scores: tuple[float, ...] = ()
        overlap_rate = 0.0
        if provider_result_lists:
            # Share one canonicalize cache across the two RRF calls and
            # this function's own overlap/score lookups, so each distinct
            # raw URL is canonicalized at most once per rank_and_finalize.
            key_for = _memoize_canonicalize(canonicalize_url)
            # 1. RRF once into provider_consensus
            provider_consensus_with_scores = reciprocal_rank_fusion(
                provider_result_lists,
                k=rrf_k,
                canonicalize=key_for,
            )
            provider_consensus_list = [res for res, _ in provider_consensus_with_scores]

            first_stage_url_scores = {
                key_for(res.link): score for res, score in provider_consensus_with_scores
            }
            url_occurrences: Counter[str] = Counter(
                key_for(result.link) for results in provider_result_lists for result in results
            )
            overlap_rate = (
                sum(count > 1 for count in url_occurrences.values()) / len(url_occurrences)
                if url_occurrences
                else 0.0
            )

            # 2. Compute BM25 ranking over those same canonical candidates
            bm25_scores = await score_candidates_async(
                run.plan.relevance_query if run.plan else run.request.query,
                [_candidate_text(result) for result in provider_consensus_list],
            )
            bm25_order_indices = sorted(
                range(len(provider_consensus_list)),
                key=lambda index: (-(bm25_scores[index] if bm25_scores[index] > 0 else 0.0), index),
            )
            bm25_order = [provider_consensus_list[index] for index in bm25_order_indices]

            # 3. Run RRF a second time over provider_consensus and bm25_order
            hybrid_order_with_scores = reciprocal_rank_fusion(
                [provider_consensus_list, bm25_order],
                k=rrf_k,
                canonicalize=key_for,
            )
            second_stage_scores = tuple(score for _, score in hybrid_order_with_scores)

            # Keep provider consensus, hybrid RRF, and later neural scores distinct.
            for res, second_stage_score in hybrid_order_with_scores:
                url_key = key_for(res.link)
                first_stage_score = first_stage_url_scores.get(url_key, 0.0)
                res_updated = res.model_copy(
                    update={
                        "score": second_stage_score,
                        "hybrid_rrf_score": second_stage_score,
                        "provider_consensus_rrf_score": first_stage_score,
                    }
                )
                merged.append(res_updated)

        dc.merged_candidates = list(merged)
        span.set_attribute("search.merge_algorithm", "provider_consensus_rrf_then_bm25_rrf")

        providers_used_set: set[str] = set()
        for outcome in outcomes:
            providers_used_set.update(outcome.attempted_provider_names)
        ranked_pool: list[WebSearchResult] = []
        rerank_provider: str | None = None
        rerank_model: str | None = None
        if merged:
            # Cross-encoder query construction receives the goal separately; RankLLM
            # intentionally receives only the normalized relevance query.
            reranked = await rerank_results(
                run.plan.normalized_query if run.plan else run.request.query,
                [result.model_copy() for result in merged],
                research_goal=run.request.research_goal,
                query_type_hint=(run.plan.understanding.intent if run.plan else None),
                run_key=run.run_key,
                session_id=run.session_id,
            )
            ranked_pool = list(reranked.results)
            rerank_provider = reranked.provider
            rerank_model = reranked.model
            ctx = reranked.embedding_context
            if ctx is not None:
                dc.candidate_embeddings = [
                    {"url": c.url, "text": c.text, "dense": list(c.dense)}
                    for c in ctx.candidates[:40]
                ]
                if ctx.query_embedding:
                    dc.query_embedding = list(ctx.query_embedding)
        if dc.query_embedding is None and embedding_task is not None:
            if embedding_task.done() and embedding_task.cancelled():
                logger.warning("Shared embedding task was cancelled; continuing without it")
            else:
                try:
                    vec = await asyncio.shield(embedding_task)
                    dc.query_embedding = list(vec)
                except asyncio.CancelledError:
                    if embedding_task.cancelled():
                        logger.warning("Shared embedding task was cancelled; continuing without it")
                    else:
                        raise
                except Exception as exc:
                    logger.warning("Failed to retrieve query embedding: %s", exc)
        run.rerank_metadata.update(
            {
                "merge_algorithm": "provider_consensus_rrf_then_bm25_rrf",
                "effective_rrf_k": rrf_k,
                "provider_list_count": len(provider_result_lists),
                "overlap_rate": overlap_rate,
                "zero_list_degradation": len(provider_result_lists) == 0,
                "single_list_degradation": len(provider_result_lists) == 1,
                "bm25_scores": tuple(bm25_scores),
                "first_stage_scores": tuple(first_stage_url_scores.values()),
                "second_stage_scores": second_stage_scores,
                "reranker_provider": rerank_provider,
                "reranker_model": rerank_model,
            }
        )
        if merged:
            run.rerank_metadata["funnel_counts"] = reranked.funnel_counts
        final_results = ranked_pool
        candidate_count = len(merged)
        returned = len(final_results)
        providers_used = sorted(
            {provider for result in final_results for provider in (result.providers or [])}
        )
        dc.merge_counts = {
            "merged_count": len(merged),
            "candidate_count": candidate_count,
            "reranked_count": len(ranked_pool),
            "final_result_count": returned,
            "branch_count": len(outcomes),
            "provider_count": len(providers_used_set),
        }
        if merged:
            dc.rerank_stage_summaries.extend([s.model_dump() for s in reranked.stage_summaries])
        dc.phase_timings["search.rank"] = (time.monotonic() - rank_started) * 1000.0
        span.set_attribute("search.merged_count", len(merged))
        span.set_attribute("search.final_count", returned)
        return WebSearchResponse(
            query=run.request.query,
            results=final_results,
            total_results=returned,
            providers_used=providers_used,
            warnings=_stable_warnings(outcomes) or None,
        )
