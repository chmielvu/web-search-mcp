"""Core response ranking and finalization."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from ..models import (
    FilterStats,
    ProviderWarning,
)
from ..rerank.bm25 import score_candidates_async
from ..rerank.pipeline import rerank_results
from ..settings import settings
from ..telemetry.spans import get_tracer
from ..utils.url_canonicalize import canonicalize_url
from .blocklist import filter_blocked_results
from .contracts import BranchOutcome, SearchRun
from .evidence import render_search_hit_text
from .filters import filter_results_by_window, parse_published_date
from .merge import memoize_canonicalize, reciprocal_rank_fusion
from .postprocess import apply_domain_boost
from .types import ScoredHit, SearchHit, SearchRunResult

logger = logging.getLogger(__name__)


def _candidate_text(result: SearchHit) -> str:
    return render_search_hit_text(result, max_chars=4000)


@dataclass(frozen=True, slots=True)
class _ExpansionEvidence:
    query: str
    support_count: int
    adapters: tuple[str, ...]


def _collect_expansion_evidence(
    outcomes: tuple[BranchOutcome, ...],
    *,
    source_query: str,
) -> tuple[_ExpansionEvidence, ...]:
    source_key = " ".join(source_query.split()).casefold()
    display_by_key: dict[str, str] = {}
    supporters_by_key: dict[str, set[tuple[str, str]]] = {}
    adapters_by_key: dict[str, set[str]] = {}
    encounter_order: dict[str, int] = {}
    for outcome in outcomes:
        for call in outcome.calls:
            supporter = (call.adapter, " ".join(call.query.split()).casefold())
            candidates = list(call.expansion)
            if call.integrity is not None and call.integrity.spelling:
                candidates.append(call.integrity.spelling)
            seen_in_call: set[str] = set()
            for raw_query in candidates:
                query = " ".join(raw_query.split())
                key = query.casefold()
                if not query or key == source_key or key in seen_in_call:
                    continue
                seen_in_call.add(key)
                if key not in encounter_order:
                    encounter_order[key] = len(encounter_order)
                    display_by_key[key] = query
                supporters_by_key.setdefault(key, set()).add(supporter)
                adapters_by_key.setdefault(key, set()).add(call.adapter)
    return tuple(
        _ExpansionEvidence(
            query=display_by_key[key],
            support_count=len(supporters_by_key[key]),
            adapters=tuple(sorted(adapters_by_key[key])),
        )
        for key in sorted(
            encounter_order,
            key=lambda item: (-len(supporters_by_key[item]), encounter_order[item]),
        )
    )


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
    """Classify result recency from its published date.

    ``fresh``: published within ``settings.freshness_max_age_days`` (default
    90). Future-dated pages (bad site clocks / SEO spam) clamp to fresh —
    they are not stale, and the date is untrustworthy either way.
    ``dated``: older than the window. ``unknown``: missing or unparseable.
    """
    if not published_date:
        return "unknown"
    parsed = parse_published_date(published_date)
    if parsed is None:
        return "unknown"
    age_days = (date.today() - parsed).days
    if age_days <= settings.freshness_max_age_days:
        return "fresh"
    return "dated"


def _build_fetch_hint_query(result: ScoredHit) -> str:
    return result.hit.url


def attach_agent_evidence(results: list[ScoredHit]) -> list[ScoredHit]:
    updated: list[ScoredHit] = []
    for idx, res in enumerate(results, start=1):
        pc = len(res.providers) if res.providers else 1
        freshness = _build_freshness_signal(res.hit.published)
        hint_query = _build_fetch_hint_query(res)
        updated.append(
            replace(
                res,
                final_rank=idx,
                citation_id=f"c{idx}",
                evidence_final=res.final_score,
                evidence_semantic=res.cross_encoder_score,
                evidence_lexical=res.retrieval_rrf_score,
                evidence_consensus=pc,
                freshness_signal=freshness,
                fetch_hint_query=hint_query,
            )
        )
    return updated


async def rank_and_finalize(
    run: SearchRun,
    outcomes: tuple[BranchOutcome, ...],
    *,
    embedding_task: Awaitable[Sequence[float]] | None,
) -> SearchRunResult:
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
        expansion_evidence = _collect_expansion_evidence(
            outcomes,
            source_query=run.plan.normalized_query if run.plan else run.request.query,
        )
        dc.provider_expansions = [
            {
                "query": signal.query,
                "support_count": signal.support_count,
                "adapters": list(signal.adapters),
            }
            for signal in expansion_evidence
        ]
        supported_expansions = tuple(
            signal.query for signal in expansion_evidence if signal.support_count >= 2
        )[:2]
        lexical_query = run.plan.relevance_query if run.plan else run.request.query
        if supported_expansions:
            lexical_query = "\n".join((lexical_query, *supported_expansions))[:1000]

        # RRF voters are the per-(branch, provider) ranked lists exactly as
        # the providers returned them: one list per independent retrieval,
        # never re-ranked or re-enumerated before fusion. Cross-branch and
        # cross-provider agreement therefore both accumulate through the RRF
        # sum itself, which is the signal the multi-rewrite branch fan-out
        # exists to produce.
        #
        # Duplicate-vote guard: identical (provider, query) pairs -- the same
        # engine re-issued the same rewritten query text, e.g. when a rewrite
        # slot collapses back to the normalized query -- return one
        # information-free duplicate list. Only those exact pairs are dropped;
        # lists from distinct query texts all vote independently.
        #
        # Weight splitting: a provider queried from N distinct branches
        # contributes N lists whose weights sum to the provider's configured
        # weight (settings.rrf_provider_weights). This preserves the
        # per-provider total influence those weights were tuned for while
        # bounding any single URL's maximum contribution from that provider
        # at weight / (rrf_k + 1), independent of branch count. Lists are
        # ordered deterministically by (branch_index, provider_name) so fused
        # scores are reproducible across runs with identical retrieval output.
        rrf_result_lists: list[list[SearchHit]] = []
        rrf_list_weights: list[float] = []
        seen_call_queries: set[tuple[str, str]] = set()
        duplicate_lists_dropped = 0
        call_lists: list[tuple[str, list[SearchHit]]] = []
        for outcome in outcomes:
            for call in outcome.calls:
                call_key = (call.adapter, outcome.branch.query)
                if call_key in seen_call_queries:
                    duplicate_lists_dropped += 1
                    continue
                seen_call_queries.add(call_key)
                filtered = filter_blocked_results(list(call.hits))
                if not filtered:
                    continue
                call_lists.append((call.adapter, filtered))

        lists_per_provider: Counter[str] = Counter(provider_name for provider_name, _ in call_lists)
        for provider_name, filtered in call_lists:
            provider_weight = settings.rrf_provider_weights.get(provider_name, 1.0)
            rrf_result_lists.append(filtered)
            rrf_list_weights.append(provider_weight / lists_per_provider[provider_name])

        merged: list[ScoredHit] = []
        rrf_k = settings.rrf_k
        bm25_scores: list[float] = []
        overlap_rate = 0.0
        if rrf_result_lists:
            # Track overlap rate across providers
            url_occurrences: Counter[str] = Counter(
                key_for(result.url) for results in rrf_result_lists for result in results
            )
            overlap_rate = (
                sum(count > 1 for count in url_occurrences.values()) / len(url_occurrences)
                if url_occurrences
                else 0.0
            )

            # 1. BM25 corpus: one entry per canonical URL, best variant =
            #    longest snippet (the same policy reciprocal_rank_fusion
            #    applies when lists disagree about a URL). BM25 acts as a
            #    complementary lexical signal alongside the semantic/dense
            #    retrieval from providers.
            raw_by_url: dict[str, SearchHit] = {}
            for results in rrf_result_lists:
                for result in results:
                    url_key = key_for(result.url)
                    existing = raw_by_url.get(url_key)
                    if existing is None or len(result.snippet or "") > len(existing.snippet or ""):
                        raw_by_url[url_key] = result
            all_raw_results = list(raw_by_url.values())

            try:
                bm25_scores = await score_candidates_async(
                    lexical_query,
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

            # 2. Single RRF: fuse the per-call provider lists + BM25 as an
            #    additional independent ranking signal. BM25 contributes
            #    lexical relevance; provider calls contribute semantic/dense
            #    relevance. RRF naturally surfaces documents that perform
            #    well across modalities and across independent query
            #    rewrites, with per-provider total influence governed by the
            #    configured provider weights.
            fused_lists = list(rrf_result_lists)
            fused_weights = list(rrf_list_weights)
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
            # 3. Wrap fused hits into ScoredHit
            for hit, score, providers in fused_with_scores:
                merged.append(
                    ScoredHit(
                        hit=hit,
                        retrieval_rrf_score=score,
                        providers=providers,
                    )
                )
        window = run.request.options.temporal
        if window is not None and not window.is_empty:
            merged, dropped_range, dropped_undated = filter_results_by_window(
                merged,
                window=window,
                get_published_date=lambda item: item.hit.published,
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
        span.set_attribute("search.merge_algorithm", "branch_call_rrf_with_bm25")

        providers_used_set: set[str] = set()
        for outcome in outcomes:
            providers_used_set.update(outcome.attempted_provider_names)
        ranked_pool: list[ScoredHit] = []
        rerank_provider: str | None = None
        rerank_model: str | None = None
        if merged:
            # Cross-encoder query construction receives the goal separately; RankLLM
            # intentionally receives only the normalized relevance query.
            reranked = await rerank_results(
                run.plan.normalized_query if run.plan else run.request.query,
                list(merged),
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
                "merge_algorithm": "branch_call_rrf_with_bm25",
                "effective_rrf_k": rrf_k,
                "provider_list_count": len(rrf_result_lists),
                "overlap_rate": overlap_rate,
                "zero_list_degradation": len(rrf_result_lists) == 0,
                "single_list_degradation": len(rrf_result_lists) == 1,
                "duplicate_lists_dropped": duplicate_lists_dropped,
                "bm25_scores": tuple(bm25_scores),
                "reranker_provider": rerank_provider,
                "reranker_model": rerank_model,
                "provider_expansion_count": len(expansion_evidence),
                "bm25_expansion_queries": supported_expansions,
            }
        )
        if merged:
            run.rerank_metadata["funnel_counts"] = reranked.funnel_counts
        final_ordered = apply_domain_boost(ranked_pool, run.request.domain_boost)
        final_results = attach_agent_evidence(final_ordered)
        candidate_count = len(merged)
        returned = len(final_results)
        providers_used = tuple(
            sorted({provider for result in final_results for provider in (result.providers or ())})
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
        return SearchRunResult(
            query=run.request.query,
            hits=tuple(final_results),
            total_results=returned,
            providers_used=providers_used,
            warnings=tuple(warnings),
            intent=(
                str(run.plan.understanding.intent)
                if (run.plan is not None and run.plan.understanding is not None)
                else (run.diagnostics.intent or None)
            ),
            filter_stats=filter_stats,
        )
