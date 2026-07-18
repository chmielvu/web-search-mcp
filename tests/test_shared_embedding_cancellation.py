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
    request.query = "cancellation test"
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
    )
    from kindly_web_search_mcp_server.search.provider_registry import _make_adapter

    async def mock_qdrant_func(query, num_results, **kwargs):
        # Await the query_embedding, which is shielded
        emb = await kwargs["query_embedding"]
        return [WebSearchResult(title="Qdrant Hit", link="https://qdrant.com", snippet="qdrant")]

    def mock_get_def(name):
        return ProviderDefinition(
            name=name,
            adapter_module="qdrant",
            adapter_function="search_qdrant",
            description="mock qdrant",
            default_timeout_seconds=0.1,
            requires_embedding=True,
        )

    def mock_get_adapter(name):
        async def adapter(query, num_results, **kwargs):
            q_emb = kwargs.get("query_embedding")
            if q_emb is not None:
                kwargs["query_embedding"] = asyncio.shield(q_emb)
            return await mock_qdrant_func(query, num_results, **kwargs)

        return adapter

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


@pytest.mark.asyncio
async def test_cancel_and_drain_tasks_bounds_cancellation_resistant_child() -> None:
    from kindly_web_search_mcp_server.utils.task_scope import cancel_and_drain_tasks

    embedding_event = asyncio.Event()
    cancellation_suppressed_event = asyncio.Event()
    release_suppression_event = asyncio.Event()

    async def get_embedding():
        await embedding_event.wait()
        return [0.1] * 1024

    embedding_task = asyncio.create_task(get_embedding())
    shielded_embedding = asyncio.shield(embedding_task)

    async def resistant_child():
        try:
            await shielded_embedding
        except asyncio.CancelledError:
            cancellation_suppressed_event.set()
            await release_suppression_event.wait()
            raise

    child_task = asyncio.create_task(resistant_child())
    await asyncio.sleep(0.001)

    t0 = time.monotonic()
    abandoned = await cancel_and_drain_tasks([child_task], drain_seconds=0.05)
    t1 = time.monotonic()

    assert t1 - t0 < 0.2
    assert child_task in abandoned
    await asyncio.wait_for(cancellation_suppressed_event.wait(), timeout=0.1)

    assert not embedding_task.cancelled()
    embedding_event.set()
    res = await embedding_task
    assert len(res) == 1024

    release_suppression_event.set()
    try:
        await child_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_retrieve_budget_uses_bounded_drain_without_cancelling_shared_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kindly_web_search_mcp_server.search.provider_catalog import (
        ProviderDefinition,
    )
    from kindly_web_search_mcp_server.search.provider_registry import _make_adapter

    embedding_event = asyncio.Event()
    release_provider_event = asyncio.Event()

    async def dummy_embed():
        await embedding_event.wait()
        return [0.1] * 1024

    embedding_task = asyncio.create_task(dummy_embed())

    async def mock_qdrant_func(query, num_results, **kwargs):
        try:
            await kwargs["query_embedding"]
        except asyncio.CancelledError:
            await release_provider_event.wait()
            raise
        return [WebSearchResult(title="Qdrant Hit", link="https://qdrant.com", snippet="qdrant")]

    def mock_get_def(name):
        return ProviderDefinition(
            name=name,
            adapter_module="qdrant",
            adapter_function="search_qdrant",
            description="mock qdrant",
            default_timeout_seconds=0.1,
            requires_embedding=True,
        )

    def mock_get_adapter(name):
        async def adapter(query, num_results, **kwargs):
            q_emb = kwargs.get("query_embedding")
            if q_emb is not None:
                kwargs["query_embedding"] = asyncio.shield(q_emb)
            return await mock_qdrant_func(query, num_results, **kwargs)

        return adapter

    monkeypatch.setattr(retrieval, "get_provider_definition", mock_get_def)
    monkeypatch.setattr(retrieval, "get_provider_adapter", mock_get_adapter)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.1)

    import kindly_web_search_mcp_server.search.retrieval as ret_mod

    orig_drain = ret_mod.cancel_and_drain_tasks

    async def mock_drain(tasks):
        return await orig_drain(tasks, drain_seconds=0.05)

    monkeypatch.setattr(ret_mod, "cancel_and_drain_tasks", mock_drain)

    run = _run(_branch("qdrant"))

    t0 = time.monotonic()
    outcomes = await retrieval.retrieve_branches(run, embedding_task=embedding_task)
    t1 = time.monotonic()

    assert t1 - t0 < 0.5

    calls = outcomes[0].provider_calls
    assert len(calls) == 1
    assert calls[0]["provider"] == "qdrant"
    assert calls[0]["status"] == "incomplete"
    assert calls[0]["error_type"] == "retrieve_budget"

    assert not embedding_task.cancelled()
    embedding_event.set()
    res = await embedding_task
    assert len(res) == 1024

    release_provider_event.set()


@pytest.mark.asyncio
async def test_retrieve_caller_cancellation_is_reraised_after_bounded_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kindly_web_search_mcp_server.search.provider_catalog import (
        ProviderDefinition,
    )
    from kindly_web_search_mcp_server.search.provider_registry import _make_adapter

    release_provider_event = asyncio.Event()

    async def mock_qdrant_func(query, num_results, **kwargs):
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            await release_provider_event.wait()
            raise
        return []

    def mock_get_def(name):
        return ProviderDefinition(
            name=name,
            adapter_module="qdrant",
            adapter_function="search_qdrant",
            description="mock qdrant",
            default_timeout_seconds=5.0,
            requires_embedding=False,
        )

    def mock_get_adapter(name):
        async def adapter(query, num_results, **kwargs):
            return await mock_qdrant_func(query, num_results, **kwargs)

        return adapter

    monkeypatch.setattr(retrieval, "get_provider_definition", mock_get_def)
    monkeypatch.setattr(retrieval, "get_provider_adapter", mock_get_adapter)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 5.0)

    import kindly_web_search_mcp_server.search.retrieval as ret_mod

    orig_drain = ret_mod.cancel_and_drain_tasks

    async def mock_drain(tasks):
        return await orig_drain(tasks, drain_seconds=0.05)

    monkeypatch.setattr(ret_mod, "cancel_and_drain_tasks", mock_drain)

    run = _run(_branch("qdrant"))

    async def cancel_retrieve_caller():
        await asyncio.sleep(0.05)
        retrieve_task.cancel()

    retrieve_task = asyncio.create_task(retrieval.retrieve_branches(run, embedding_task=None))
    canceller_task = asyncio.create_task(cancel_retrieve_caller())

    t0 = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await retrieve_task
    t1 = time.monotonic()

    assert t1 - t0 < 0.5

    release_provider_event.set()
    await canceller_task
