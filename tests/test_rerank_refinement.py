"""Comprehensive verification tests for rerank pipeline refinement."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import weakref

import pytest

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank.cohere import _COHERE_CLIENTS
from kindly_web_search_mcp_server.rerank.llm_rerank import _ranked_permutation
from kindly_web_search_mcp_server.rerank.models import RerankResult
from kindly_web_search_mcp_server.rerank.openrouter import _OPENROUTER_CLIENTS
from kindly_web_search_mcp_server.rerank.stage_runner import _apply_ranked_stage
from kindly_web_search_mcp_server.rerank.stages import (
    apply_entity_overlap_boost,
    apply_ranked_results,
    normalize_scores_minmax,
)
from kindly_web_search_mcp_server.rerank.voyage import _parse_rerank_results
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
        score=score,
        published_date=published_date,
    )


def test_flat_score_normalization_preserves_order() -> None:
    """normalize_scores_minmax on flat scores returns linear spread [1.0 .. 0.0]."""
    assert normalize_scores_minmax([]) == []
    assert normalize_scores_minmax([0.5]) == [1.0]
    result = normalize_scores_minmax([0.8, 0.8, 0.8])
    assert len(result) == 3
    assert result[0] == 1.0
    assert result[1] == 0.5
    assert result[2] == 0.0


def test_apply_ranked_results_score_clamped_to_one() -> None:
    """Recency boost cannot push final score above 1.0."""
    candidates = [_make_candidate(0, score=0.9, published_date="2026-08-18T00:00:00Z")]
    ranked_results = [RerankResult(index=0, score=1.0)]
    updated_candidates, _, _, _ = apply_ranked_results(
        candidates,
        ranked_results,
        preserve_raw_scores=True,
        update_score=True,
        recency_weight=0.5,
    )
    assert updated_candidates[0].score is not None
    assert updated_candidates[0].score <= 1.0


def test_apply_entity_overlap_boost_immutability() -> None:
    """apply_entity_overlap_boost returns a new list and does not mutate in place."""
    cand0 = _make_candidate(0, score=0.5)
    cand1 = _make_candidate(1, score=0.6)
    cand0.entities = [EntitySpan(text="fastapi", label="package", start=0, end=7, confidence=1.0)]
    cand1.entities = [EntitySpan(text="django", label="package", start=0, end=6, confidence=1.0)]
    candidates = [cand0, cand1]

    logger = logging.getLogger("test")
    query_entities = [EntitySpan(text="fastapi", label="package", start=0, end=7, confidence=1.0)]
    new_candidates = apply_entity_overlap_boost(
        candidates,
        query_entities=query_entities,
        entity_overlap_enabled=True,
        entity_overlap_weight=0.2,
        logger=logger,
    )
    assert new_candidates is not candidates
    assert new_candidates[0].score is not None
    assert new_candidates[0].score > 0.5
    assert candidates[0].score == 0.5  # original preserved


def test_voyage_parse_accepts_partial_results() -> None:
    """Voyage _parse_rerank_results accepts len(results) <= document_count."""
    data = {
        "data": [
            {"index": 2, "relevance_score": 0.95},
            {"index": 0, "relevance_score": 0.80},
        ]
    }
    parsed = _parse_rerank_results(data, document_count=5)
    assert parsed == [(2, 0.95), (0, 0.80)]

    # Over document count should raise ValueError
    data_too_many = {
        "data": [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.8},
            {"index": 2, "relevance_score": 0.7},
        ]
    }
    with pytest.raises(ValueError, match="expected at most 2"):
        _parse_rerank_results(data_too_many, document_count=2)


def test_rankllm_synthetic_scoring_is_rrf_style() -> None:
    """_ranked_permutation uses 1.0 / (60 + position)."""
    fake_result = SimpleNamespace(
        candidates=[SimpleNamespace(docid=1), SimpleNamespace(docid=0)]
    )
    ranked = _ranked_permutation(fake_result, candidate_count=2)
    assert ranked[0].index == 1
    assert ranked[0].score == 1.0 / 60.0
    assert ranked[1].index == 0
    assert ranked[1].score == 1.0 / 61.0


@pytest.mark.asyncio
async def test_apply_ranked_stage_preserves_unranked_tail() -> None:
    """_apply_ranked_stage preserves unranked candidates when provider returns partial results."""
    input_candidates = [_make_candidate(i) for i in range(10)]
    # Provider only returned 3 results
    ranked_results = [
        RerankResult(index=5, score=0.9),
        RerankResult(index=2, score=0.8),
        RerankResult(index=8, score=0.7),
    ]
    logger = logging.getLogger("test")
    outcome = await _apply_ranked_stage(
        stage_name="cross_encoder",
        provider="cohere",
        model="rerank-v4.0",
        input_tokens=None,
        output_tokens=None,
        input_candidates=input_candidates,
        ranked_results=ranked_results,
        duration_seconds=0.1,
        query_type_hint=None,
        payload_json={},
        run_key="test-run",
        main_span=MagicMock(),
        logger=logger,
        output_limit=10,
    )
    # Must preserve all 10 candidates
    assert len(outcome.candidates) == 10
    # First 3 are the reranked ones
    assert [c.link for c in outcome.candidates[:3]] == [
        "https://example.com/5",
        "https://example.com/2",
        "https://example.com/8",
    ]
    # Remaining 7 are unranked candidates in their original incoming order
    assert [c.link for c in outcome.candidates[3:]] == [
        "https://example.com/0",
        "https://example.com/1",
        "https://example.com/3",
        "https://example.com/4",
        "https://example.com/6",
        "https://example.com/7",
        "https://example.com/9",
    ]


def test_cohere_and_openrouter_use_weakkeydictionary() -> None:
    """Client registries are WeakKeyDictionary instances."""
    assert isinstance(_COHERE_CLIENTS, weakref.WeakKeyDictionary)
    assert isinstance(_OPENROUTER_CLIENTS, weakref.WeakKeyDictionary)


def test_features_status_reranking_enabled() -> None:
    """Status reports Reranking as statically enabled."""
    status = get_features_status()
    assert "**Reranking**: ✓ Enabled" in status


@pytest.mark.asyncio
async def test_code_search_rerank_strictly_bounds_output() -> None:
    """rerank_code_hits output never exceeds max_results."""
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
            return_value=SimpleNamespace(
                ranked=[RerankResult(index=i, score=0.9 - 0.01 * i) for i in range(20)],
                provider_id="cohere",
                model="rerank-v4.0-fast",
                input_tokens=100,
                output_tokens=50,
                duration_seconds=0.2,
                raw_response=None,
                error=None,
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
