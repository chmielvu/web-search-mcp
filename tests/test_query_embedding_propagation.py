"""Regression tests for query embedding propagation from rerank context into diagnostics and analytics."""

from __future__ import annotations

import httpx
from unittest.mock import AsyncMock

import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.models import (
    CandidateEmbedding,
    RerankEmbeddingContext,
    RerankOutput,
)
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    QueryBranch,
    ProviderRankedResults,
    SearchRun,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.diagnostics import build_diagnostics
from kindly_web_search_mcp_server.search.ranking import rank_and_finalize


def _make_run(stub_result: WebSearchResult) -> SearchRun:
    """Construct a minimal SearchRun + BranchOutcome for rank_and_finalize."""
    branch = QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
        query="q",
        provider_names=("test",),
        max_results=15,
    )
    outcome = BranchOutcome(
        branch=branch,
        attempted_provider_names=("test",),
        provider_ranked_results=(ProviderRankedResults(0, branch.role, "test", (stub_result,)),),
    )
    run = SearchRun(
        request=WebSearchRequest(query="q", research_goal="goal"),
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    return run, outcome


@pytest.fixture
def vec_1024():
    return [0.001 * i for i in range(1024)]


@pytest.fixture
def stub_result():
    return WebSearchResult(
        link="https://example.test/x",
        title="Test Result",
        snippet="Test snippet",
        score=0.5,
    )


@pytest.mark.asyncio
async def test_rank_and_finalize_propagates_query_embedding_when_context_is_not_none(
    monkeypatch: pytest.MonkeyPatch, stub_result: WebSearchResult, vec_1024: list[float]
) -> None:
    """Collector receives query_embedding from RerankEmbeddingContext."""
    run, outcome = _make_run(stub_result)

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.ranking.rerank_results",
        AsyncMock(
            return_value=RerankOutput(
                results=[stub_result],
                embedding_context=RerankEmbeddingContext(
                    query_embedding=list(vec_1024),
                    candidates=[
                        CandidateEmbedding(
                            url=stub_result.link,
                            text="t",
                            dense=list(vec_1024),
                        )
                    ],
                ),
            )
        ),
    )

    await rank_and_finalize(run, (outcome,), embedding_task=None)
    assert run.diagnostics.query_embedding == vec_1024
    assert len(run.diagnostics.candidate_embeddings) == 1


@pytest.mark.asyncio
async def test_build_diagnostics_reports_query_embedding_dim_from_rerank_context(
    monkeypatch: pytest.MonkeyPatch, stub_result: WebSearchResult, vec_1024: list[float]
) -> None:
    """build_diagnostics projects query_embedding_dim=1024 from the collector."""
    run, outcome = _make_run(stub_result)

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.ranking.rerank_results",
        AsyncMock(
            return_value=RerankOutput(
                results=[stub_result],
                embedding_context=RerankEmbeddingContext(
                    query_embedding=list(vec_1024),
                    candidates=[
                        CandidateEmbedding(
                            url=stub_result.link,
                            text="t",
                            dense=list(vec_1024),
                        )
                    ],
                ),
            )
        ),
    )

    await rank_and_finalize(run, (outcome,), embedding_task=None)
    diag = build_diagnostics(run, total_latency_ms=0.0)
    assert diag.embeddings.query_embedding_dim == 1024
    assert diag.embeddings.candidate_count == 1


@pytest.mark.asyncio
async def test_persist_search_outcome_writes_query_embedding_row_when_context_is_not_none(
    monkeypatch: pytest.MonkeyPatch, stub_result: WebSearchResult, vec_1024: list[float]
) -> None:
    """persist_search_outcome dispatches a query_embeddings DuckDB write with the correct payload."""
    run, outcome = _make_run(stub_result)

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.ranking.rerank_results",
        AsyncMock(
            return_value=RerankOutput(
                results=[stub_result],
                embedding_context=RerankEmbeddingContext(
                    query_embedding=list(vec_1024),
                    candidates=[
                        CandidateEmbedding(
                            url=stub_result.link,
                            text="t",
                            dense=list(vec_1024),
                        )
                    ],
                ),
            )
        ),
    )

    query_writes: list[dict] = []
    candidate_writes: list[dict] = []
    no_op_calls: list[str] = []

    def fake_insert_query_embeddings(**kwargs: dict) -> None:
        query_writes.append(kwargs)

    def fake_insert_candidate_embeddings(**kwargs: dict) -> None:
        candidate_writes.append(kwargs)

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.duckdb_store.insert_query_embeddings",
        fake_insert_query_embeddings,
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.duckdb_store.insert_candidate_embeddings",
        fake_insert_candidate_embeddings,
    )

    for name in (
        "insert_final_results",
        "insert_search_run",
        "insert_search_branches",
        "insert_provider_calls",
        "insert_search_candidates",
        "insert_rerank_stages",
    ):

        def make_recorder(n: str):
            def _rec(**kwargs: dict) -> None:
                no_op_calls.append(n)

            return _rec

        monkeypatch.setattr(
            f"kindly_web_search_mcp_server.analytics.duckdb_store.{name}",
            make_recorder(name),
        )

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.async_writes.dispatch_duckdb_write",
        lambda name, writer: writer(),
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.quality_metrics.compute_search_quality",
        lambda run_key: None,
    )

    response = await rank_and_finalize(run, (outcome,), embedding_task=None)
    run.succeed(response)
    from kindly_web_search_mcp_server.search.outcomes import persist_search_outcome

    await persist_search_outcome(run)

    assert len(query_writes) == 1, query_writes
    qw = query_writes[0]
    assert qw["run_key"] == "t-run"
    assert qw["embedding"] == vec_1024
    assert qw["model_id"] == "intfloat/multilingual-e5-large-instruct"
    assert len(candidate_writes) >= 1, candidate_writes
    assert candidate_writes[0]["embedding"] == vec_1024
