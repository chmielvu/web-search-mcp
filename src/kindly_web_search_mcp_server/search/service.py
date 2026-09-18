"""Shared MCP/CLI web-search application service."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

import httpx

from ..ml import embed_query, embed_texts
from .contracts import SearchRun, WebSearchRequest
from .evidence import render_search_hit_text
from .outcomes import submit_search_outcome
from .planning import plan_search
from .ranking import rank_and_finalize
from .retrieval import retrieve_branches
from .types import SearchRunResult

LOGGER = logging.getLogger(__name__)
_INDEX_TASKS: set[asyncio.Task[None]] = set()


async def run_search_core(run: SearchRun) -> SearchRunResult:
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
        run.diagnostics.total_latency_ms = (time.monotonic() - core_started) * 1000.0
        _schedule_web_results_indexing(run, response)
        return response
    finally:
        if embedding_task is not None and not embedding_task.done():
            embedding_task.cancel()
            await asyncio.gather(embedding_task, return_exceptions=True)


def _schedule_web_results_indexing(run: SearchRun, response: SearchRunResult) -> None:
    """Fire-and-forget write of final results into the remote Qdrant index.

    Non-fatal by contract: indexing failures never affect the search
    response. The index feeds the qdrant read provider on later searches.
    """

    async def _index() -> None:
        try:
            from ..index import index_final_results

            results = list(response.hits)
            if not results:
                return
            texts = [render_search_hit_text(result.hit, max_chars=4000) for result in results]
            dense = run.diagnostics.candidate_embeddings
            by_url = {c.get("url", ""): c.get("dense") for c in dense}
            embeddings = [by_url.get(r.hit.url) for r in results]
            if not embeddings or any(e is None for e in embeddings):
                embeddings = list(
                    await embed_texts(
                        texts,
                        timeout=20.0,
                    )
                )
            vectors = [list(e) for e in embeddings if e is not None]
            if len(vectors) != len(results):
                LOGGER.debug(
                    "web-results indexing skipped: %d/%d embeddings resolvable",
                    len(vectors),
                    len(results),
                )
                return
            entities = (
                [entity.model_dump(mode="json") for entity in run.plan.understanding.entities]
                if run.plan is not None
                else None
            )
            await index_final_results(
                run.request.query,
                results,
                vectors,
                texts=texts,
                intent=run.diagnostics.intent,
                entities=entities,
            )
        except Exception:
            LOGGER.debug("web-results indexing failed (non-fatal)", exc_info=True)

    task = asyncio.create_task(_index(), name=f"search.index.{run.run_key}")
    _INDEX_TASKS.add(task)
    task.add_done_callback(_INDEX_TASKS.discard)


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
) -> SearchRunResult | tuple[SearchRunResult, SearchRun]:
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
