"""Core response ranking and finalization."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Sequence
from datetime import date
import logging
import time
from typing import Literal

from ..models import (
    FilterStats,
    ProviderWarning,
    WebSearchEvidenceScore,
    WebSearchFetchHint,
    WebSearchResponse,
    WebSearchResult,
)
from ..rerank.bm25 import score_candidates_async
from ..rerank.pipeline import rerank_results
from ..settings import settings
from ..telemetry.spans import get_tracer
from .blocklist import filter_blocked_results
from .contracts import BranchOutcome, SearchRun
from .filters import filter_results_by_window, parse_published_date
from .merge import memoize_canonicalize, reciprocal_rank_fusion
from .postprocess import apply_domain_boost
from ..utils.url_canonicalize import canonicalize_url

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


def _build_freshness_signal(
    published_date: str | None,
) -> Literal["fresh", "dated", "unknown"]:
    if not published_date:
        return "unknown"
    parsed = parse_published_date(published_date)
    if parsed is None:
        return "unknown"
    age_days = (date.today() - parsed).days
    if age_days <= 90:
        return "fresh"
    return "dated"


def _build_fetch_hint(result: WebSearchResult, rank: int) -> WebSearchFetchHint:
    cross = result.cross_encoder_score
    if cross is not None:
        if cross >= 0.70:
            confidence = "high"
            why = "Strong semantic relevance from cross-encoder; fetch full page for grounded context."
        elif cross >= 0.50:
            confidence = "medium"
            why = "Moderate semantic relevance; fetch to verify snippet details."
        else:
            confidence = "low"
            why = "Lower semantic relevance; fetch only if higher-ranked sources are insufficient."
    else:
        if rank <= 3 or (result.final_score is not None and result.final_score >= 0.70):
            confidence = "high"
            why = "Top-ranked search result; fetch full page for grounded context."
        elif rank <= 7 or (result.final_score is not None and result.final_score >= 0.40):
            confidence = "medium"
            why = "Relevant search result; fetch to inspect complete source details."
        else:
            confidence = "low"
            why = "Lower-ranked result; fetch only if higher-ranked sources are insufficient."

    return WebSearchFetchHint(
        action="fetch",
        tool="fetch",
        query={"url": result.link},
        why=why,
        confidence=confidence,
    )


def attach_agent_evidence(results: list[WebSearchResult]) -> list[WebSearchResult]:
    updated: list[WebSearchResult] = []
    for idx, res in enumerate(results, start=1):
        pc = len(res.providers) if res.providers else 1
        evidence_score = WebSearchEvidenceScore(
            final=res.final_score,
            semantic=res.cross_encoder_score,
            lexical=res.retrieval_rrf_score,
            engine_consensus=pc,
        )
        freshness = _build_freshness_signal(res.published_date)
        hint = _build_fetch_hint(res, idx)
        updated.append(
            res.model_copy(
                update={
                    "final_rank": idx,
                    "citation_id": f"c{idx}",
                    "evidence_score": evidence_score,
                    "freshness_signal": freshness,
                    "fetch_hint": hint,
                }
            )
        )
    return updated


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
        key_for = memoize_canonicalize(canonicalize_url)
        warnings = _stable_warnings(outcomes)
        warnings.extend(
            ProviderWarning(provider="filters", error=message, error_type="filter")
            for message in run.request.pre_warnings
        )
        filter_stats: FilterStats | None = None

        # Collapse per-branch provider lists into one list per distinct
        # provider before fusion. Branches like "original" and "free" query
        # the same provider set (e.g. ddg, qdrant, searxng, degoog), so a
        # provider queried from multiple branches must contribute exactly
        # one fused list -- otherwise it gets counted (and RRF-boosted)
        # once per branch, rewarding branch volume instead of independent
        # provider evidence. Each URL keeps its best (lowest) rank across
        # every branch that surfaced it.
        provider_hit_ranks: dict[str, dict[str, tuple[int, WebSearchResult]]] = {}
        provider_order: list[str] = []
        for outcome in outcomes:
            for prr in outcome.provider_ranked_results:
                filtered = filter_blocked_results(list(prr.results))
                if not filtered:
                    continue
                bucket = provider_hit_ranks.setdefault(prr.provider_name, {})
                if prr.provider_name not in provider_order:
                    provider_order.append(prr.provider_name)
                for rank, result in enumerate(filtered, start=1):
                    url_key = key_for(result.link)
                    existing = bucket.get(url_key)
                    if existing is None or rank < existing[0]:
                        bucket[url_key] = (rank, result)

        provider_result_lists: list[list[WebSearchResult]] = []
        provider_list_weights: list[float] = []
        for provider_name in provider_order:
            ordered = sorted(provider_hit_ranks[provider_name].values(), key=lambda pair: pair[0])
            provider_result_lists.append([result for _, result in ordered])
            provider_list_weights.append(settings.rrf_provider_weights.get(provider_name, 1.0))

        merged: list[WebSearchResult] = []
        rrf_k = settings.rrf_k
        bm25_scores: list[float] = []
        overlap_rate = 0.0
        if provider_result_lists:
            # Track overlap rate across providers
            url_occurrences: Counter[str] = Counter(
                key_for(result.link) for results in provider_result_lists for result in results
            )
            overlap_rate = (
                sum(count > 1 for count in url_occurrences.values()) / len(url_occurrences)
                if url_occurrences
                else 0.0
            )

            # 1. Compute BM25 independently on all raw provider results
            #    (deduplicated by canonical URL, keeping best snippet).
            #    BM25 acts as a complementary lexical signal alongside
            #    the semantic/dense retrieval from providers.
            raw_by_url: dict[str, WebSearchResult] = {}
            for results in provider_result_lists:
                for result in results:
                    url_key = key_for(result.link)
                    existing = raw_by_url.get(url_key)
                    if existing is None or len(result.snippet or "") > len(existing.snippet or ""):
                        raw_by_url[url_key] = result
            all_raw_results = list(raw_by_url.values())

            try:
                bm25_scores = await score_candidates_async(
                    run.plan.relevance_query if run.plan else run.request.query,
                    [_candidate_text(result) for result in all_raw_results],
                )
            except Exception as exc:
                logger.warning("BM25 scoring failed, continuing without BM25 signal: %s", exc)
                bm25_scores = [0.0] * len(all_raw_results)
            bm25_order_indices = sorted(
                [idx for idx in range(len(all_raw_results)) if bm25_scores[idx] > 0.0],
                key=lambda idx: (-bm25_scores[idx], idx),
            )
            bm25_order = [all_raw_results[idx] for idx in bm25_order_indices]

            # 2. Single RRF: fuse provider result lists + BM25 as an
            #    additional independent ranking signal. BM25 contributes
            #    lexical relevance; providers contribute semantic/dense
            #    relevance. RRF naturally surfaces documents that perform
            #    well across both modalities. Each list is weighted so a
            #    scarce, strong signal counts for more than a high-volume,
            #    weaker one, independent of how many lists it contributes.
            fused_lists = list(provider_result_lists)
            fused_weights = list(provider_list_weights)
            if bm25_order:
                fused_lists.append(bm25_order)
                fused_weights.append(settings.rrf_bm25_weight)
            fused_with_scores = reciprocal_rank_fusion(
                fused_lists,
                k=rrf_k,
                canonicalize=key_for,
                weights=fused_weights,
            )

            # 3. Apply RRF scores to merged results
            for res, score in fused_with_scores:
                res_updated = res.model_copy(
                    update={
                        "retrieval_rrf_score": score,
                    }
                )
                merged.append(res_updated)
        window = run.request.options.temporal
        if window is not None and not window.is_empty:
            merged, dropped_range, dropped_undated = filter_results_by_window(
                merged,
                window=window,
                get_published_date=lambda item: item.published_date,
                get_providers=lambda item: item.providers or None,
                include_undated=run.request.include_undated,
            )
            policy = (
                "keep_all"
                if run.request.include_undated is True
                else ("drop_all" if run.request.include_undated is False else "capability_default")
            )
            filter_stats = FilterStats(
                dropped_out_of_range=dropped_range,
                dropped_undated=dropped_undated,
                undated_policy=policy,
            )
            if dropped_range:
                warnings.append(
                    ProviderWarning(
                        provider="filters",
                        error=(
                            f"Post-filter removed {dropped_range} result(s) outside "
                            f"{window.start}..{window.end}."
                        ),
                        error_type="filter",
                    )
                )
            if dropped_undated:
                warnings.append(
                    ProviderWarning(
                        provider="filters",
                        error=(
                            f"Dropped {dropped_undated} undated result(s) under the "
                            f"{policy} window policy."
                        ),
                        error_type="filter",
                    )
                )

        dc.merged_candidates = list(merged)
        span.set_attribute("search.merge_algorithm", "provider_rrf_with_bm25")

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
                query_type_hint=(
                    run.plan.understanding.intent if (run.plan and run.plan.understanding) else None
                ),
                run_key=run.run_key,
                session_id=run.session_id,
                reranking_instructions=run.request.reranking_instructions,
            )
            ranked_pool = list(reranked.results)
            dc.overflow_ranked = [(item.stage, item.result) for item in reranked.overflow_items]
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
            is_task_or_future = isinstance(embedding_task, (asyncio.Task, asyncio.Future))
            if is_task_or_future and embedding_task.done() and embedding_task.cancelled():
                logger.warning("Shared embedding task was cancelled; continuing without it")
            else:
                try:
                    vec = await asyncio.shield(embedding_task)
                    dc.query_embedding = list(vec)
                except asyncio.CancelledError:
                    if is_task_or_future and embedding_task.cancelled():
                        logger.warning("Shared embedding task was cancelled; continuing without it")
                    else:
                        raise
                except Exception as exc:
                    logger.warning("Failed to retrieve query embedding: %s", exc)
        run.rerank_metadata.update(
            {
                "merge_algorithm": "provider_rrf_with_bm25",
                "effective_rrf_k": rrf_k,
                "provider_list_count": len(provider_result_lists),
                "overlap_rate": overlap_rate,
                "zero_list_degradation": len(provider_result_lists) == 0,
                "single_list_degradation": len(provider_result_lists) == 1,
                "bm25_scores": tuple(bm25_scores),
                "reranker_provider": rerank_provider,
                "reranker_model": rerank_model,
            }
        )
        if merged:
            run.rerank_metadata["funnel_counts"] = reranked.funnel_counts
        final_ordered = apply_domain_boost(ranked_pool, run.request.domain_boost)
        final_results = attach_agent_evidence(final_ordered)
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
            warnings=warnings or None,
            intent=(
                str(run.plan.understanding.intent)
                if (run.plan is not None and run.plan.understanding is not None)
                else (run.diagnostics.intent or None)
            ),
            query_shaping=(run.diagnostics.query_shaping or None) or None,
            filter_stats=filter_stats,
        )
