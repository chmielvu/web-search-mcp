"""Shared MCP/CLI web-search application service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import httpx

from ..embeddings.hf_inference import embed_query
from ..models import WebSearchResponse
from .contracts import SearchRun, WebSearchRequest
from .outcomes import submit_search_outcome
from .planning import plan_search
from .ranking import rank_and_finalize
from .retrieval import retrieve_branches

async def run_search_core(run: SearchRun) -> WebSearchResponse:
    core_started = time.monotonic()
    plan = await plan_search(run)
    needs_embedding = any(
        "qdrant" in branch.provider_names for branch in plan.branches
    )
    embedding_task: asyncio.Task[Sequence[float]] | None = None
    if needs_embedding:
        embedding_task = asyncio.create_task(
            embed_query(plan.relevance_query), name=f"search.embedding.{run.run_key}"
        )
    try:
        outcomes = await retrieve_branches(run, embedding_task=embedding_task)
        response = await rank_and_finalize(run, outcomes, embedding_task=embedding_task)
        run.diagnostics.total_latency_ms = (time.monotonic() - core_started) * 1000.0
        return response
    finally:
        if embedding_task is not None and not embedding_task.done():
            embedding_task.cancel()
            await asyncio.gather(embedding_task, return_exceptions=True)


async def execute_web_search(
    request: WebSearchRequest,
    *,
    http_client: httpx.AsyncClient,
    run_key: str,
    tool_call_id: str | None = None,
    session_id: str | None = None,
    progress: object | None = None,
    return_diagnostics: bool = False,
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
