"""Regression tests for search/ranking.py.

Two regressions:
- The rerank-side copy must absorb in-place mutations (entity-overlap
  etc.) so the analytics snapshot in `dc.merged_candidates` is preserved.
- The shared canonicalize cache must collapse repeated raw URLs across
  both RRF invocations and the in-function lookups.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from unittest.mock import patch

import httpx
import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.models import RerankOutput
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    ProviderRankedResults,
    QueryBranch,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.filters import TemporalWindow
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.ranking import rank_and_finalize
from kindly_web_search_mcp_server.search.service import SearchRun
from kindly_web_search_mcp_server.utils.public_output import to_public_web_search


def _stub(index: int, score: float = 0.5) -> WebSearchResult:
    return WebSearchResult(
        title=f"Doc {index}",
        link=f"https://host{index}.test/{index}",
        snippet=f"Evidence {index}",
        domain=f"host{index}.test",
        providers=["provider-a"],
        retrieval_rrf_score=score,
    )


def _make_run(
    results: list[WebSearchResult],
    *,
    request: WebSearchRequest | None = None,
) -> tuple[SearchRun, BranchOutcome]:
    branch = QueryBranch(
        role=BranchRole.ORIGINAL,
        query="q",
        provider_names=("test",),
        max_results=15,
    )
    outcome = BranchOutcome(
        branch=branch,
        attempted_provider_names=("test",),
        provider_ranked_results=(ProviderRankedResults(0, branch.role, "test", tuple(results)),),
    )
    run = SearchRun(
        request=(
            request if request is not None else WebSearchRequest(query="q", research_goal="goal")
        ),
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    return run, outcome


@pytest.mark.asyncio
async def test_dc_merged_candidates_unchanged_when_rerank_mutates_inputs() -> None:
    """The rerank-side copy must absorb in-place mutations.

    The reranker receives a distinct candidate list; mutations to
    ``final_score`` must not corrupt the merged analytics snapshot.
    """
    run, outcome = _make_run([_stub(0, 0.7), _stub(1, 0.3)])
    try:
        before_scores: list[float | None] = []
        rerank_candidates: list[WebSearchResult] = []

        async def fake_rerank(
            _query: str,
            candidates: list[WebSearchResult],
            **_kwargs: object,
        ) -> RerankOutput:
            before_scores.extend(c.final_score for c in candidates)
            for c in candidates:
                c.final_score = 99.0
            rerank_candidates.extend(candidates)
            return RerankOutput(
                results=list(candidates),
                embedding_context=None,
                provider="stub",
                model="stub",
            )

        with patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            side_effect=fake_rerank,
        ):
            await rank_and_finalize(run, (outcome,), embedding_task=None)

        snapshot = run.diagnostics.merged_candidates
        assert snapshot, "expected analytics snapshot to be populated"
        # Snapshot must equal the pre-mutation scores captured inside the stub.
        assert [s.final_score for s in snapshot] == before_scores
        # Rerank must have received a distinct list (object identity).
        assert rerank_candidates[0] is not snapshot[0]
        # ...and that rerank-side list was actually mutated to 99.0.
        assert rerank_candidates[0].final_score == 99.0
    finally:
        await run.http_client.aclose()


@pytest.mark.asyncio
async def test_rank_and_finalize_canonicalizes_each_distinct_url_once() -> None:
    """`rank_and_finalize` shares one canonicalize cache across BM25 scoring,
    RRF fusion, and its own overlap/score lookups.

    Without sharing, the same `canonicalize_url` is invoked up to 5
    times per distinct raw URL per request. With the shared
    `_memoize_canonicalize`, each distinct raw URL is canonicalized
    exactly once even though the function touches the same links via
    BM25, the single RRF fusion, the `url_occurrences` counter,
    and the per-result loop.
    """
    inputs = [_stub(0, 0.7), _stub(1, 0.3)]
    run, outcome = _make_run(inputs)
    try:
        import kindly_web_search_mcp_server.search.ranking as ranking_mod

        original = ranking_mod.canonicalize_url
        call_count = {"n": 0}
        per_raw: dict[str, int] = {}

        def counting(raw: str) -> str:
            call_count["n"] += 1
            per_raw[raw] = per_raw.get(raw, 0) + 1
            return original(raw)

        async def fake_rerank(
            _query: str,
            candidates: list[WebSearchResult],
            **_kwargs: object,
        ) -> RerankOutput:
            return RerankOutput(
                results=list(candidates),
                embedding_context=None,
                provider="stub",
                model="stub",
            )

        ranking_mod.canonicalize_url = counting
        try:
            with patch(
                "kindly_web_search_mcp_server.search.ranking.rerank_results",
                side_effect=fake_rerank,
            ):
                response = await rank_and_finalize(run, (outcome,), embedding_task=None)
        finally:
            ranking_mod.canonicalize_url = original

        # Each distinct raw URL was canonicalized exactly once.
        assert per_raw == {r.link: 1 for r in inputs}
        # And the total call count equals the number of distinct URLs.
        assert call_count["n"] == len(inputs)
        # Response must surface both results so the pipeline ran end-to-end.
        assert len(response.results) == len(inputs)
    finally:
        await run.http_client.aclose()


@pytest.mark.asyncio
async def test_rank_and_finalize_collapses_same_provider_across_branches() -> None:
    """A provider queried from multiple branches (e.g. `original` + `free`
    both firing the same engine) must contribute exactly one fused list to
    RRF, not one list per branch -- otherwise a URL surfaced by both
    branches gets RRF-boosted once per branch, rewarding branch volume
    instead of independent provider evidence.
    """
    shared = _stub(0, 0.7)  # surfaced by "test" in both branches, rank 1 each time
    only_original = _stub(1, 0.3)  # surfaced by "test" only in the original branch, rank 2

    original_branch = QueryBranch(
        role=BranchRole.ORIGINAL, query="q", provider_names=("test",), max_results=15
    )
    free_branch = QueryBranch(
        role=BranchRole.FREE, query="q", provider_names=("test",), max_results=15
    )
    original_outcome = BranchOutcome(
        branch=original_branch,
        attempted_provider_names=("test",),
        provider_ranked_results=(
            ProviderRankedResults(0, original_branch.role, "test", (shared, only_original)),
        ),
    )
    free_outcome = BranchOutcome(
        branch=free_branch,
        attempted_provider_names=("test",),
        provider_ranked_results=(ProviderRankedResults(1, free_branch.role, "test", (shared,)),),
    )
    run = SearchRun(
        request=WebSearchRequest(query="q", research_goal="goal"),
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    try:

        async def fake_rerank(
            _query: str,
            candidates: list[WebSearchResult],
            **_kwargs: object,
        ) -> RerankOutput:
            return RerankOutput(
                results=list(candidates),
                embedding_context=None,
                provider="stub",
                model="stub",
            )

        with patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            side_effect=fake_rerank,
        ):
            response = await rank_and_finalize(
                run, (original_outcome, free_outcome), embedding_task=None
            )

        by_link = {r.link: r for r in response.results}
        # Collapsed to one "test" list: rank-1 contribution counted once,
        # not once per branch (which would double it to 2/61).
        assert by_link[shared.link].retrieval_rrf_score == pytest.approx(1.0 / 61.0)
        assert by_link[only_original.link].retrieval_rrf_score == pytest.approx(1.0 / 62.0)
    finally:
        await run.http_client.aclose()


@pytest.mark.asyncio
async def test_rank_and_finalize_applies_configured_provider_weights() -> None:
    """`settings.rrf_provider_weights` must scale each provider's RRF
    contribution end-to-end through `rank_and_finalize`, not just inside
    the standalone `reciprocal_rank_fusion` unit."""
    from kindly_web_search_mcp_server.settings import settings

    exa_result = _stub(0, 0.7)
    serper_result = _stub(1, 0.3)

    exa_branch = QueryBranch(
        role=BranchRole.SEMANTIC_EXA, query="q", provider_names=("exa",), max_results=15
    )
    serp_branch = QueryBranch(
        role=BranchRole.SERP1, query="q", provider_names=("serper",), max_results=15
    )
    exa_outcome = BranchOutcome(
        branch=exa_branch,
        attempted_provider_names=("exa",),
        provider_ranked_results=(ProviderRankedResults(0, exa_branch.role, "exa", (exa_result,)),),
    )
    serp_outcome = BranchOutcome(
        branch=serp_branch,
        attempted_provider_names=("serper",),
        provider_ranked_results=(
            ProviderRankedResults(1, serp_branch.role, "serper", (serper_result,)),
        ),
    )
    run = SearchRun(
        request=WebSearchRequest(query="q", research_goal="goal"),
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    try:

        async def fake_rerank(
            _query: str,
            candidates: list[WebSearchResult],
            **_kwargs: object,
        ) -> RerankOutput:
            return RerankOutput(
                results=list(candidates),
                embedding_context=None,
                provider="stub",
                model="stub",
            )

        with patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            side_effect=fake_rerank,
        ):
            response = await rank_and_finalize(
                run, (exa_outcome, serp_outcome), embedding_task=None
            )

        by_link = {r.link: r for r in response.results}
        exa_weight = settings.rrf_provider_weights.get("exa", 1.0)
        serper_weight = settings.rrf_provider_weights.get("serper", 1.0)
        assert exa_weight > serper_weight, "test assumes exa is configured above the default weight"
        assert by_link[exa_result.link].retrieval_rrf_score == pytest.approx(exa_weight / 61.0)
        assert by_link[serper_result.link].retrieval_rrf_score == pytest.approx(
            serper_weight / 61.0
        )
        # The stronger, unit-weight-normalized signal outranks the weaker one.
        assert response.results[0].link == exa_result.link
    finally:
        await run.http_client.aclose()


@pytest.mark.asyncio
async def test_rank_and_finalize_attaches_evidence_fields() -> None:
    res1 = WebSearchResult(
        title="Doc 1",
        link="https://example.com/1",
        snippet="Snippet 1",
        domain="example.com",
        published_date="2026-08-01",
        providers=["test"],
        final_score=0.9,
        retrieval_rrf_score=0.8,
        cross_encoder_score=0.85,
    )
    res2 = WebSearchResult(
        title="Doc 2",
        link="https://example.com/2",
        snippet="Snippet 2",
        domain="example.com",
        published_date="2026-01-01",
        providers=["test", "test2", "test3"],
        final_score=0.6,
        retrieval_rrf_score=0.5,
        cross_encoder_score=0.55,
    )
    res3 = WebSearchResult(
        title="Doc 3",
        link="https://example.com/3",
        snippet="Snippet 3",
        domain="example.com",
        published_date=None,
        providers=["p1", "p2", "p3", "p4", "p5"],
        final_score=0.2,
        retrieval_rrf_score=0.2,
        cross_encoder_score=0.30,
    )
    run, outcome = _make_run([res1, res2, res3])

    async def fake_rerank(
        _query: str,
        candidates: Sequence[WebSearchResult],
        **_kwargs: object,
    ) -> RerankOutput:
        scored = [
            candidates[0].model_copy(update={"final_score": 0.9}),
            candidates[1].model_copy(update={"final_score": 0.6}),
            candidates[2].model_copy(update={"final_score": 0.2}),
        ]
        return RerankOutput(
            results=scored,
            provider="test",
            model="test",
            stage_summaries=(),
        )

    try:
        with patch("kindly_web_search_mcp_server.search.ranking.rerank_results", fake_rerank):
            response = await rank_and_finalize(run, (outcome,), embedding_task=None)

        results = response.results
        assert len(results) == 3
        # Assert citation IDs
        assert results[0].citation_id == "c1"
        assert results[1].citation_id == "c2"
        assert results[2].citation_id == "c3"

        # Assert evidence_score breakdown
        assert results[0].evidence_score is not None
        assert results[0].evidence_score.final == 0.9
        assert results[0].evidence_score.semantic == 0.85
        assert results[0].evidence_score.lexical == pytest.approx(1.0 / 61.0)
        assert results[0].evidence_score.engine_consensus == 1
        assert results[1].evidence_score is not None
        assert results[1].evidence_score.engine_consensus == 3

        assert results[2].evidence_score is not None
        assert results[2].evidence_score.engine_consensus == 5

        # Assert freshness signals
        assert results[0].freshness_signal == "fresh"
        assert results[1].freshness_signal == "dated"
        assert results[2].freshness_signal == "unknown"

        # Assert fetch_hint continuation
        assert results[0].fetch_hint is not None
        assert results[0].fetch_hint.action == "fetch"
        assert results[0].fetch_hint.tool == "fetch"
        assert results[0].fetch_hint.query == {"url": results[0].link}
        assert results[0].fetch_hint.confidence == "high"

        assert results[1].fetch_hint is not None
        assert results[1].fetch_hint.confidence == "medium"

        assert results[2].fetch_hint is not None
        assert results[2].fetch_hint.confidence == "low"

    finally:
        await run.http_client.aclose()


@pytest.mark.asyncio
async def test_rank_and_finalize_skips_reranker_when_temporal_filter_removes_all() -> None:
    stale = _stub(0).model_copy(update={"published_date": "2010-01-01"})
    request = WebSearchRequest(
        query="q",
        research_goal="goal",
        options=SearchOptions(
            temporal=TemporalWindow(start=date(2026, 1, 1), end=date(2026, 12, 31))
        ),
        include_undated=False,
    )
    run, outcome = _make_run([stale], request=request)

    async def unexpected_rerank(*_args: object, **_kwargs: object) -> RerankOutput:
        raise AssertionError("reranker must not run for an empty filtered pool")

    try:
        with patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            side_effect=unexpected_rerank,
        ) as rerank_mock:
            response = await rank_and_finalize(run, (outcome,), embedding_task=None)
        rerank_mock.assert_not_awaited()
    finally:
        await run.http_client.aclose()

    assert response.results == []
    assert response.total_results == 0
    assert response.filter_stats is not None
    assert response.filter_stats.dropped_out_of_range == 1
    assert response.filter_stats.dropped_undated == 0
    assert any(w.provider == "filters" for w in response.warnings or [])


@pytest.mark.asyncio
async def test_rank_and_finalize_domain_boost_sets_public_order_and_score() -> None:
    request = WebSearchRequest(
        query="q",
        research_goal="goal",
        domain_boost=("host1.test",),
    )
    run, outcome = _make_run([_stub(0), _stub(1)], request=request)

    async def fake_rerank(
        _query: str,
        candidates: Sequence[WebSearchResult],
        **_kwargs: object,
    ) -> RerankOutput:
        ordered = sorted(candidates, key=lambda candidate: candidate.link)
        scored = [
            candidate.model_copy(
                update={"final_score": 0.9 if candidate.domain == "host0.test" else 0.8}
            )
            for candidate in ordered
        ]
        return RerankOutput(results=scored, provider="stub", model="stub")

    try:
        with patch(
            "kindly_web_search_mcp_server.search.ranking.rerank_results",
            side_effect=fake_rerank,
        ):
            response = await rank_and_finalize(run, (outcome,), embedding_task=None)
        public = to_public_web_search(
            query=response.query,
            intent=None,
            query_variants=None,
            hits=list(response.results),
            overflow_items=[],
            warnings=None,
        )
    finally:
        await run.http_client.aclose()

    assert [result.link for result in response.results] == [
        "https://host1.test/1",
        "https://host0.test/0",
    ]
    assert [result.final_rank for result in response.results] == [1, 2]
    assert [result.citation_id for result in response.results] == ["c1", "c2"]
    assert [hit.citation_id for hit in public.results] == ["c1", "c2"]
    assert [hit.url for hit in public.results] == [result.link for result in response.results]
    assert public.results[0].score == pytest.approx(response.results[0].final_score)
