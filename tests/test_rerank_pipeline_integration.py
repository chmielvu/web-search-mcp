from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.core import rerank_results
from kindly_web_search_mcp_server.rerank.models import (
    CandidateEmbedding,
    RerankEmbeddingContext,
    RerankOutput,
)
from kindly_web_search_mcp_server.rerank.stage_runner import RankedStageOutcome
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    DiagnosticsCollector,
    ProviderRankedResults,
    QueryBranch,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.ranking import rank_and_finalize
from kindly_web_search_mcp_server.settings import settings


def _candidate(index: int, *, host: str | None = None) -> WebSearchResult:
    resolved_host = host or f"host{index}.test"
    return WebSearchResult(
        title=f"Document {index}",
        link=f"https://{resolved_host}/{index}",
        snippet=f"Evidence {index}",
        domain=resolved_host,
        providers=["provider-a"],
        provider_count=1,
        score=0.03,
        hybrid_rrf_score=0.03,
    )


def _stage_outcome(
    candidates: list[WebSearchResult],
    *,
    provider: str,
    model: str,
    cross: bool,
) -> RankedStageOutcome:
    updated = [
        candidate.model_copy(update={"cross_relevance_score": 0.9 - index / 1000} if cross else {})
        for index, candidate in enumerate(candidates)
    ]
    return RankedStageOutcome(
        candidates=updated,
        provider=provider,
        model=model,
        stage_name="cross_encoder" if cross else "llm_reranker",
        input_count=len(candidates),
        output_count=len(candidates),
        duration_seconds=0.0,
        relevance_scores=[0.9 - index / 1000 for index in range(len(candidates))],
        max_score=0.9,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_count", [100, 101])
async def test_strict_boundaries_preserve_rankllm_window_and_every_tail(
    candidate_count: int,
) -> None:
    candidates = [
        _candidate(index, host="cluster.test" if candidate_count == 101 and index < 20 else None)
        for index in range(candidate_count)
    ]
    bi_rank = AsyncMock()
    if candidate_count == 101:
        bi_order = list(reversed(candidates))
        embeddings = [
            CandidateEmbedding(
                url=candidate.link,
                text="embedded",
                dense=[1.0, 0.0] if position < 20 else [0.0, float(position + 1)],
            )
            for position, candidate in enumerate(bi_order)
        ]
        bi_rank.return_value = (
            bi_order,
            RerankEmbeddingContext(query_embedding=[1.0], candidates=embeddings),
        )

    async def fake_cross(**kwargs):
        assert len(kwargs["candidates"]) == 100
        return _stage_outcome(
            list(reversed(kwargs["candidates"]))[:30],
            provider="cohere_fast",
            model="rerank-v4.0-fast",
            cross=True,
        )

    llm_seen: list[str] = []

    async def fake_llm(**kwargs):
        assert len(kwargs["candidates"]) == 30
        llm_seen.extend(candidate.link for candidate in kwargs["candidates"])
        return _stage_outcome(
            list(reversed(kwargs["candidates"])),
            provider="openrouter",
            model="openai/gpt-oss-20b:free",
            cross=False,
        )

    with (
        patch(
            "kindly_web_search_mcp_server.rerank.conditional_bi.bi_encoder_rank",
            bi_rank,
        ),
        patch(
            "kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage",
            AsyncMock(side_effect=fake_cross),
        ),
        patch(
            "kindly_web_search_mcp_server.rerank.core.run_llm_stage",
            AsyncMock(side_effect=fake_llm),
        ),
        patch.object(settings, "rerank_score_thresholds_json", "{}"),
    ):
        output = await rerank_results(
            "plain query",
            candidates,
            top_k=candidate_count,
            research_goal="Find primary evidence.",
            query_type_hint="comparison",
            precomputed_embedding=[1.0],
        )

    if candidate_count == 100:
        bi_rank.assert_not_awaited()
    else:
        bi_rank.assert_awaited_once()
    assert len(llm_seen) == 30
    assert len(output.results) == candidate_count
    assert len({candidate.link for candidate in output.results}) == candidate_count
    assert all(candidate.hybrid_rrf_score == 0.03 for candidate in output.results)
    assert output.results[-1].link in {candidate.link for candidate in candidates}


@pytest.mark.asyncio
async def test_provider_consensus_bm25_fusion_keeps_all_score_identities() -> None:
    first = [_candidate(index) for index in range(3)]
    second = [first[1].model_copy(), first[2].model_copy(), first[0].model_copy()]
    branch = QueryBranch(
        role=BranchRole.ORIGINAL,
        query="query",
        provider_names=("a", "b"),
        max_results=10,
    )
    outcome = BranchOutcome(
        branch=branch,
        attempted_provider_names=("a", "b"),
        provider_ranked_results=(
            ProviderRankedResults(0, branch.role, "a", tuple(first)),
            ProviderRankedResults(0, branch.role, "b", tuple(second)),
        ),
    )
    request = WebSearchRequest(
        query="query",
        research_goal="Find primary evidence.",
        num_results=15,
        options=SearchOptions(),
    )
    run = SimpleNamespace(
        request=request,
        plan=SimpleNamespace(
            relevance_query="query\nResearch goal: Find primary evidence.",
            normalized_query="query",
            understanding=SimpleNamespace(intent="comparison"),
        ),
        diagnostics=DiagnosticsCollector(),
        rerank_metadata={},
        run_key="integration-fusion",
        session_id=None,
    )

    async def identity_rerank(_query, candidates, **_kwargs):
        return RerankOutput(results=candidates, provider="bypass", model=None)

    with (
        patch(
            "kindly_web_search_mcp_server.search.ranking.score_candidates_async",
            return_value=[0.1, 0.0, 0.9],
        ),
        patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            AsyncMock(side_effect=identity_rerank),
        ) as rerank_results_mock,
    ):
        response = await rank_and_finalize(run, (outcome,), embedding_task=None)

    assert len(response.results) == 3
    # The goal is a separate cross-encoder input and must not leak into RankLLM.
    assert rerank_results_mock.await_args.args[0] == "query"
    assert rerank_results_mock.await_args.kwargs["research_goal"] == "Find primary evidence."
    assert len({result.link for result in response.results}) == 3
    assert all(result.score == result.hybrid_rrf_score for result in response.results)
    # provider_consensus_rrf_score is deprecated; single-stage RRF uses only hybrid_rrf_score
    assert all(result.provider_consensus_rrf_score is None for result in response.results)
    assert run.rerank_metadata["merge_algorithm"] == "provider_rrf_with_bm25"
