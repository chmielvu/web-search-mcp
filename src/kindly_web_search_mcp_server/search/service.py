"""Shared MCP/CLI web-search application service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import httpx

from ..embeddings import embed_query
from ..models import FilterStats, ProviderWarning, WebSearchResponse
from .contracts import SearchRun, WebSearchRequest
from .filters import filter_results_by_window
from .outcomes import submit_search_outcome
from .planning import plan_search
from .postprocess import apply_domain_boost
from .ranking import rank_and_finalize
from .retrieval import retrieve_branches


async def run_search_core(run: SearchRun) -> WebSearchResponse:
    core_started = time.monotonic()
    plan = await plan_search(run)
    needs_embedding = any("qdrant" in branch.provider_names for branch in plan.branches)
    embedding_task: asyncio.Task[Sequence[float]] | None = None
    if needs_embedding:
        embedding_task = asyncio.create_task(
            embed_query(plan.relevance_query), name=f"search.embedding.{run.run_key}"
        )
    try:
        outcomes = await retrieve_branches(run, embedding_task=embedding_task)
        response = await rank_and_finalize(run, outcomes, embedding_task=embedding_task)
        response = _apply_post_processing(run, response)
        run.diagnostics.total_latency_ms = (time.monotonic() - core_started) * 1000.0
        return response
    finally:
        if embedding_task is not None and not embedding_task.done():
            embedding_task.cancel()
            await asyncio.gather(embedding_task, return_exceptions=True)


def _apply_post_processing(run: SearchRun, response: WebSearchResponse) -> WebSearchResponse:
    """Single-source post-rank transformations: temporal filter + domain boost.

    Merges caller pre-warnings, applies the absolute-window safety net, and
    reorders results by domain boost — all on the model, so the tool layer
    returns the response as-is.
    """
    request = run.request
    warnings = list(response.warnings or [])
    warnings.extend(
        ProviderWarning(provider="filters", error=message, error_type="filter")
        for message in request.pre_warnings
    )

    window = request.options.temporal
    if window is not None and not window.is_empty:
        kept_rows, dropped_range, dropped_undated = filter_results_by_window(
            response.results,
            window=window,
            get_published_date=lambda item: item.published_date,
            get_providers=lambda item: item.providers or None,
            include_undated=request.include_undated,
        )
        policy = (
            "keep_all"
            if request.include_undated is True
            else ("drop_all" if request.include_undated is False else "capability_default")
        )
        response = response.model_copy(
            update={
                "results": kept_rows,
                "filter_stats": FilterStats(
                    dropped_out_of_range=dropped_range,
                    dropped_undated=dropped_undated,
                    undated_policy=policy,
                ),
            }
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

    if request.domain_boost:
        response = response.model_copy(
            update={"results": apply_domain_boost(response.results, request.domain_boost)}
        )

    if warnings:
        response = response.model_copy(update={"warnings": warnings})
    return response


async def execute_web_search(
    request: WebSearchRequest,
    *,
    http_client: httpx.AsyncClient,
    run_key: str,
    tool_call_id: str | None = None,
    session_id: str | None = None,
    progress: object | None = None,
    return_diagnostics: bool = False,
    schedule_judges: bool = True,
) -> WebSearchResponse | tuple[WebSearchResponse, "SearchRun"]:
    """Execute a web search. When return_diagnostics=True, returns (response, run)
    so the caller can build SearchDiagnostics from run.diagnostics."""
    run = SearchRun(
        request=request,
        http_client=http_client,
        run_key=run_key,
        tool_call_id=tool_call_id,
        session_id=session_id,
        progress=progress,
        schedule_judges=schedule_judges,
    )
    try:
        response = await run_search_core(run)
    except asyncio.CancelledError:
        run.cancel("caller cancelled")
        submit_search_outcome(run)
        raise
    except Exception as exc:
        run.fail(type(exc).__name__)
        submit_search_outcome(run)
        raise
    run.succeed(response)
    submit_search_outcome(run)
    return (response, run) if return_diagnostics else response
