"""Behavior coverage for the consolidated rerank pipeline."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.llm_rerank import _ranked_permutation
from kindly_web_search_mcp_server.rerank.models import RerankResult
from kindly_web_search_mcp_server.rerank.providers import (
    RerankResponseError,
    parse_rerank_response,
    RerankProviderOutcome,
)
from kindly_web_search_mcp_server.rerank.stage_runner import _apply_ranked_stage
from kindly_web_search_mcp_server.rerank.stages import apply_ranked_results, normalize_scores_minmax
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchHit
from kindly_web_search_mcp_server.tools.code_search.reranking import rerank_code_hits
from kindly_web_search_mcp_server.tools.status import get_features_status


def _make_candidate(
    i: int, score: float = 0.5, published_date: str | None = None
) -> WebSearchResult:
    return WebSearchResult(
        title=f"Doc {i}",
        link=f"https://example.com/{i}",
        snippet=f"Snippet for doc {i}",
        retrieval_rrf_score=score,
        published_date=published_date,
    )


def test_flat_score_normalization_preserves_order() -> None:
    """Flat scores receive a deterministic descending normalized spread."""
    assert normalize_scores_minmax([]) == []
    assert normalize_scores_minmax([0.5]) == [1.0]
    result = normalize_scores_minmax([0.8, 0.8, 0.8])
    assert len(result) == 3
    assert result[0] == 1.0
    assert result[1] == 0.5
    assert result[2] == 0.0


def test_apply_ranked_results_stores_cross_and_recency_scores() -> None:
    candidates = [_make_candidate(0, score=0.9, published_date="2026-08-18T00:00:00Z")]
    ranked_results = [RerankResult(index=0, relevance_score=1.0)]
    updated_candidates, _, _, _ = apply_ranked_results(
        candidates,
        ranked_results,
        stage_name="cross_encoder",
        recency_weight=0.5,
    )
    result = updated_candidates[0]
    assert result.cross_encoder_score == 1.0
    assert result.recency_score is not None
    assert 0.0 <= result.recency_score <= 1.0
    assert result.final_score is not None
    assert result.final_score <= 1.0


def test_shared_parser_accepts_partial_results() -> None:
    data = [
        {"index": 2, "relevance_score": 0.95},
        {"index": 0, "relevance_score": 0.80},
    ]
    parsed = parse_rerank_response(data, candidate_count=5)
    assert [(item.index, item.relevance_score) for item in parsed] == [(2, 0.95), (0, 0.80)]

    with pytest.raises(RerankResponseError, match="returned 3 results"):
        parse_rerank_response(
            [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
                {"index": 2, "relevance_score": 0.7},
            ],
            candidate_count=2,
        )


def test_rankllm_synthetic_scoring_is_position_based() -> None:
    """_ranked_permutation assigns descending positional relevance scores."""
    fake_result = SimpleNamespace(candidates=[SimpleNamespace(docid=1), SimpleNamespace(docid=0)])
    ranked = _ranked_permutation(fake_result, candidate_count=2)
    assert ranked[0].index == 1
    assert ranked[0].relevance_score == 1.0 / 60.0
    assert ranked[1].index == 0
    assert ranked[1].relevance_score == 1.0 / 61.0


@pytest.mark.asyncio
async def test_apply_ranked_stage_preserves_unranked_tail() -> None:
    """A partial provider ranking keeps the unranked input-order tail."""
    input_candidates = [_make_candidate(i) for i in range(10)]
    ranked_results = [
        RerankResult(index=5, relevance_score=0.9),
        RerankResult(index=2, relevance_score=0.8),
        RerankResult(index=8, relevance_score=0.7),
    ]
    outcome = await _apply_ranked_stage(
        stage_name="cross_encoder",
        provider="cohere",
        model="rerank-v4.0",
        input_tokens=None,
        output_tokens=None,
        input_candidates=input_candidates,
        ranked_results=ranked_results,
        duration_seconds=0.1,
        run_key="test-run",
        main_span=MagicMock(),
        logger=logging.getLogger("test"),
        output_limit=10,
    )
    assert len(outcome.candidates) == 10
    assert [c.link for c in outcome.candidates[:3]] == [
        "https://example.com/5",
        "https://example.com/2",
        "https://example.com/8",
    ]
    assert [c.link for c in outcome.candidates[3:]] == [
        "https://example.com/0",
        "https://example.com/1",
        "https://example.com/3",
        "https://example.com/4",
        "https://example.com/6",
        "https://example.com/7",
        "https://example.com/9",
    ]


def test_features_status_reranking_enabled() -> None:
    status = get_features_status()
    assert "**Reranking**: ✓ Enabled" in status


@pytest.mark.asyncio
async def test_code_search_rerank_strictly_bounds_output() -> None:
    hits = [
        CodeSearchHit(
            url=f"https://github.com/owner/repo/blob/main/file{i}.py",
            repository="owner/repo",
            path=f"file{i}.py",
            score=0.1 * i,
            search_rank=i,
            provider="github",
        )
        for i in range(100)
    ]
    with patch(
        "kindly_web_search_mcp_server.tools.code_search.reranking.rerank_with_provider_fallback",
        AsyncMock(
            return_value=RerankProviderOutcome(
                ranked=[RerankResult(index=i, relevance_score=0.9 - 0.01 * i) for i in range(20)],
                provider_id="cohere",
                model="rerank-v4.0-fast",
            )
        ),
    ):
        outcome = await rerank_code_hits(
            query="test query",
            hits=hits,
            max_results=30,
            max_candidates=50,
        )
    assert len(outcome.hits) == 30
