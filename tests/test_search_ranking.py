"""Regression tests for search/ranking.py.

Two regressions:
- The rerank-side copy must absorb in-place mutations (entity-overlap
  etc.) so the analytics snapshot in `dc.merged_candidates` is preserved.
- The shared canonicalize cache must collapse repeated raw URLs across
  both RRF invocations and the in-function lookups.
"""

from __future__ import annotations

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
from kindly_web_search_mcp_server.search.ranking import rank_and_finalize
from kindly_web_search_mcp_server.search.service import SearchRun


def _stub(index: int, score: float = 0.5) -> WebSearchResult:
    return WebSearchResult(
        title=f"Doc {index}",
        link=f"https://host{index}.test/{index}",
        snippet=f"Evidence {index}",
        domain=f"host{index}.test",
        providers=["provider-a"],
        provider_count=1,
        score=score,
        hybrid_rrf_score=score,
    )


def _make_run(
    results: list[WebSearchResult],
) -> tuple[SearchRun, BranchOutcome]:
    branch = QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
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
        request=WebSearchRequest(query="q", research_goal="goal"),
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    return run, outcome


@pytest.mark.asyncio
async def test_dc_merged_candidates_unchanged_when_rerank_mutates_inputs() -> None:
    """The rerank-side copy must absorb in-place mutations.

    `apply_entity_overlap_boost` (and friends) write `candidate.score` on
    the model instance directly. Without the rerank-side copy those
    mutations would propagate into `merged` and `dc.merged_candidates`,
    corrupting the analytics snapshot.
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
            before_scores.extend(c.score for c in candidates)
            for c in candidates:
                c.score = 99.0  # mirror entity-overlap direct mutation
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
        assert [s.score for s in snapshot] == before_scores
        # Rerank must have received a distinct list (object identity).
        assert rerank_candidates[0] is not snapshot[0]
        # ...and that rerank-side list was actually mutated to 99.0.
        assert rerank_candidates[0].score == 99.0
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
