"""Core response ranking and finalization."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)
import time
from collections.abc import Awaitable, Sequence

from ..models import ProviderWarning, WebSearchResponse, WebSearchResult
from ..rerank.bm25 import score_candidates
from ..rerank.core import rerank_results
from ..telemetry.spans import get_tracer
from .blocklist import filter_blocked_results
from .contracts import BranchOutcome, SearchRun
from .merge import merge_search_results


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
        filtered_lists = [filter_blocked_results(list(outcome.results)) for outcome in outcomes]
        merged = (
            merge_search_results(
                filtered_lists,
                enable_telemetry=False,
                run_key=None,
            )
            if any(filtered_lists)
            else []
        )
        dc.merged_candidates = merged
        providers_used_set: set[str] = set()
        for outcome in outcomes:
            providers_used_set.update(outcome.attempted_provider_names)
        ranked_pool: list[WebSearchResult] = []
        rerank_provider: str | None = None
        rerank_model: str | None = None
        if merged:
            bm25_scores = score_candidates(
                run.plan.relevance_query if run.plan else run.request.query,
                [_candidate_text(result) for result in merged],
            )
            order = sorted(
                range(len(merged)),
                key=lambda index: (-(bm25_scores[index] if bm25_scores[index] > 0 else 0.0), index),
            )
            lexical = [merged[index] for index in order]
            reranked = await rerank_results(
                run.plan.relevance_query if run.plan else run.request.query,
                lexical,
                top_k=min(100, run.request.options.result_offset + run.request.num_results),
                research_goal=run.request.research_goal,
                query_type_hint=(run.plan.understanding.intent if run.plan else None),
                run_key=run.run_key,
                session_id=run.session_id,
            )
            ranked_pool = list(reranked.results)
            rerank_provider = reranked.provider
            rerank_model = reranked.model
            run.rerank_metadata.update(
                {
                    "bm25_scores": tuple(bm25_scores),
                    "reranker_provider": rerank_provider,
                    "reranker_model": rerank_model,
                }
            )
            ctx = reranked.embedding_context
            if ctx is not None:
                dc.candidate_embeddings = [
                    {"url": c.url, "text": c.text, "dense": list(c.dense)}
                    for c in ctx.candidates[:40]
                ]
                dc.query_embedding = list(ctx.query_embedding)
            elif embedding_task is not None:
                if embedding_task.done() and embedding_task.cancelled():
                    logger.warning("Shared embedding task was cancelled; continuing without it")
                else:
                    try:
                        vec = await asyncio.shield(embedding_task)
                        dc.query_embedding = list(vec)
                    except asyncio.CancelledError:
                        if embedding_task.cancelled():
                            logger.warning(
                                "Shared embedding task was cancelled; continuing without it"
                            )
                        else:
                            raise
                    except Exception as exc:
                        logger.warning("Failed to retrieve query embedding: %s", exc)
        elif embedding_task is not None:
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
        offset = run.request.options.result_offset
        final_results = ranked_pool[offset : offset + run.request.num_results]
        candidate_count = len(ranked_pool)
        returned = len(final_results)
        has_more = offset + returned < candidate_count
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
        if rerank_provider:
            dc.rerank_stage_summaries.append(
                {
                    "stage": "rerank.final",
                    "provider": rerank_provider,
                    "model": rerank_model,
                    "input_count": len(merged),
                    "output_count": len(ranked_pool),
                    "duration_ms": (time.monotonic() - rank_started) * 1000.0,
                }
            )
        dc.phase_timings["search.rank"] = (time.monotonic() - rank_started) * 1000.0
        span.set_attribute("search.merged_count", len(merged))
        span.set_attribute("search.final_count", returned)
        return WebSearchResponse(
            query=run.request.query,
            results=final_results,
            total_results=returned,
            result_window={
                "offset": offset,
                "returned": returned,
                "candidate_count": candidate_count,
                "has_more": has_more,
                "next_offset": offset + returned if has_more else None,
            },
            providers_used=providers_used,
            warnings=_stable_warnings(outcomes) or None,
        )
