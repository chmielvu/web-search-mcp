from __future__ import annotations

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kindly_web_search_mcp_server.models import WebSearchResult, WebSearchResponse
from kindly_web_search_mcp_server.search import retrieval, ranking
from kindly_web_search_mcp_server.search.contracts import (
    BranchRole,
    DiagnosticsCollector,
    QueryBranch,
    SearchRun,
)
from kindly_web_search_mcp_server.search.options import SearchOptions


def _branch(*providers: str) -> QueryBranch:
    return QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
        query="cancellation test",
        provider_names=providers,
        max_results=5,
    )


def _run(*branches: QueryBranch) -> SearchRun:
    request = MagicMock()
    request.options = SearchOptions()
    request.num_results = 5
    run = SearchRun(
        request=request,
        http_client=AsyncMock(),
        run_key="embedding-cancellation-test",
    )
    plan = MagicMock()
    plan.branches = branches
    plan.provider_arguments = {}
    plan.relevance_query = "cancellation test"
    run.plan = plan
    return run


@pytest.mark.asyncio
async def test_qdrant_cancellation_does_not_cancel_shared_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_started = asyncio.Event()

    async def dummy_embed():
        embedding_started.set()
        await asyncio.sleep(5.0)
        return [0.1, 0.2, 0.3]

    embedding_task = asyncio.create_task(dummy_embed())

    from kindly_web_search_mcp_server.search.provider_catalog import (
        ProviderDefinition,
        ProviderGroup,
    )
    from kindly_web_search_mcp_server.search.provider_registry import _make_adapter

    async def mock_qdrant_func(query, num_results, **kwargs):
        # Await the query_embedding, which is shielded
        emb = await kwargs["query_embedding"]
        return [WebSearchResult(title="Qdrant Hit", link="https://qdrant.com", snippet="qdrant")]

    def mock_get_def(name):
        return ProviderDefinition(
            name=name,
            group=ProviderGroup.FREE,
            description="mock qdrant",
            default_timeout_seconds=0.1,
            requires_embedding=True,
        )

    def mock_get_adapter(name):
        return _make_adapter(mock_qdrant_func, "qdrant")

    monkeypatch.setattr(retrieval, "get_provider_definition", mock_get_def)
    monkeypatch.setattr(retrieval, "get_provider_adapter", mock_get_adapter)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.2)

    run = _run(_branch("qdrant"))

    outcomes = await retrieval.retrieve_branches(run, embedding_task=embedding_task)

    calls = outcomes[0].provider_calls
    assert len(calls) == 1
    assert calls[0]["provider"] == "qdrant"
    assert calls[0]["status"] == "incomplete"

    # Crucial assertion: the shared embedding_task must NOT be cancelled!
    assert not embedding_task.cancelled()

    # Cleanup embedding task
    embedding_task.cancel()
    try:
        await embedding_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_rank_and_finalize_survives_cancelled_embedding_task() -> None:
    # 1. Create a pre-cancelled embedding task
    async def dummy_embed():
        await asyncio.sleep(5.0)
        return [0.1, 0.2, 0.3]

    embedding_task = asyncio.create_task(dummy_embed())
    embedding_task.cancel()
    try:
        await embedding_task
    except asyncio.CancelledError:
        pass

    assert embedding_task.cancelled()

    # 2. Run rank_and_finalize
    run = _run(_branch("qdrant"))
    outcome = retrieval.BranchOutcome(
        branch=run.plan.branches[0],
        attempted_provider_names=("qdrant",),
        results=(WebSearchResult(title="Qdrant Hit", link="https://qdrant.com", snippet="qdrant"),),
    )

    # Should complete without raising CancelledError
    response = await ranking.rank_and_finalize(
        run,
        (outcome,),
        embedding_task=embedding_task,
    )

    assert isinstance(response, WebSearchResponse)
    assert run.diagnostics.query_embedding is None
