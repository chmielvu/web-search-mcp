from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kindly_web_search_mcp_server.rerank.models import (  # noqa: E402
    CandidateEmbedding,
    RerankEmbeddingContext,
    RerankOutput,
)
from rerank_eval_calibration import calibrate_cross_thresholds  # noqa: E402
from rerank_eval_capture import materialize_diversity_windows  # noqa: E402
from rerank_eval_common import load_jsonl, validate_borderline_fixture  # noqa: E402
from rerank_eval_diversity import tune_diversity  # noqa: E402
from rerank_eval_fusion import tune_rrf  # noqa: E402

FIXTURE = Path("tests/fixtures/rerank_borderline_pairs.jsonl")
FIXTURE_CHECKSUM = "3ddfb9d039fac731d5e1063e2d16f573e5212da47fa7392a7353e64e7e2d12e0"


def _fusion_record(record_id: str = "q1") -> dict:
    urls = [f"https://example{i}.com/doc" for i in range(30)]
    documents = {
        url: {
            "title": f"Document {index}",
            "link": url,
            "snippet": f"Evidence {index}",
            "domain": f"example{index}.com",
            "providers": ["a" if index % 2 else "b"],
            "provider_count": 1,
        }
        for index, url in enumerate(urls)
    }
    return {
        "id": record_id,
        "query": "test query",
        "research_goal": "Find authoritative test evidence.",
        "intent": "comparison",
        "provider_rankings": [urls, [urls[1], urls[0], *urls[2:]]],
        "bm25_scores": {url: float(30 - index) for index, url in enumerate(urls)},
        "documents": documents,
    }


def _judge_scores(overall: float) -> dict[str, float]:
    return {
        "overall": overall,
        "research_goal_usefulness": overall,
        "source_quality": overall,
        "relevance": overall,
    }


def _diversity_record(record_id: str = "q1") -> dict:
    window = []
    for index in range(30):
        embedding = [0.0] * 31
        embedding[0 if index < 3 else index + 1] = 1.0
        window.append(
            {
                "title": f"Document {index}",
                "snippet": f"Evidence {index}",
                "url": f"https://{'cluster.test' if index < 3 else f'host{index}.test'}/{index}",
                "domain": "cluster.test" if index < 3 else f"host{index}.test",
                "providers": ["test"],
                "provider_count": 1,
                "embedding": embedding,
            }
        )
    return {
        "id": record_id,
        "query": "test query",
        "research_goal": "Find varied authoritative evidence.",
        "intent": "comparison",
        "window": window,
    }


def test_borderline_fixture_allocation_and_checksum_are_stable() -> None:
    summary = validate_borderline_fixture(load_jsonl(FIXTURE))
    assert summary == {
        "row_count": 40,
        "template_group_allocation": {
            "general_primary": 20,
            "technical_primary": 20,
        },
        "fixture_checksum": FIXTURE_CHECKSUM,
    }


@pytest.mark.asyncio
async def test_cross_calibration_means_valid_scores_and_omits_insufficient_route() -> None:
    records = load_jsonl(FIXTURE)

    async def complete(_query: str, _documents: list[str]) -> list[tuple[int, float]]:
        return [(0, 0.4)]

    calls = 0

    async def insufficient(_query: str, _documents: list[str]) -> list[tuple[int, float]]:
        nonlocal calls
        calls += 1
        if calls > 29:
            raise TimeoutError("bounded failure")
        return [(0, 0.8)]

    artifact = await calibrate_cross_thresholds(
        records,
        route_calls={
            "cohere_fast": ("rerank-v4.0-fast", complete),
            "cohere_fast_openrouter": ("cohere/rerank-v4.0-fast", insufficient),
        },
    )
    assert artifact["thresholds"] == {"cohere_fast:rerank-v4.0-fast": pytest.approx(0.4)}
    missing = artifact["routes"]["cohere_fast_openrouter:cohere/rerank-v4.0-fast"]
    assert missing["status"] == "insufficient_samples"
    assert missing["sample_count"] == 29
    assert len(missing["failures"]) == 11


def test_rrf_replay_applies_deterministic_judge_guard() -> None:
    records = [_fusion_record("q1"), _fusion_record("q2")]
    for record in records:
        record["judge_scores"] = {
            "20": _judge_scores(0.70),
            "40": _judge_scores(0.82),
            "60": _judge_scores(0.80),
            "80": _judge_scores(0.75),
        }
    artifact = tune_rrf(records)
    assert set(artifact["variants"]) == {"20", "40", "60", "80"}
    assert artifact["decision"]["selected_k"] == 40
    assert artifact["decision"]["reason"] == "qualified_judge_improvement"


@pytest.mark.asyncio
async def test_materialization_uses_selected_rrf_and_threshold_artifact() -> None:
    record = _fusion_record()
    order = list(record["documents"])
    candidates = [
        CandidateEmbedding(
            url=record["documents"][item_id]["link"],
            text="embedded",
            dense=[float(index), 1.0],
        )
        for index, item_id in enumerate(order)
    ]

    async def fake_rerank(_query, input_candidates, **_kwargs):
        context = RerankEmbeddingContext(query_embedding=[], candidates=candidates)
        return RerankOutput(
            results=input_candidates, embedding_context=context, provider="fake", model="m"
        )

    with patch("rerank_eval_capture.rerank_results", new=AsyncMock(side_effect=fake_rerank)):
        windows = await materialize_diversity_windows(
            [record],
            selected_k=40,
            thresholds={"fake:m": 0.5},
            cross_threshold_checksum="abc123",
        )
    assert windows[0]["selected_rrf_k"] == 40
    assert windows[0]["cross_threshold_checksum"] == "abc123"
    assert len(windows[0]["window"]) == 30


def test_diversity_replay_evaluates_all_variants_with_fixed_audit_metric() -> None:
    artifact = tune_diversity([_diversity_record("q1"), _diversity_record("q2")])
    assert artifact["audit_duplicate_threshold"] == 0.85
    assert len(artifact["variants"]) == 18
    assert artifact["decision"]["enabled"] is True
    winner = artifact["decision"]["winner"]
    assert winner is not None
    assert artifact["baseline"]["mean_fixed_duplicate_pair_rate"] > 0.0
