from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank import core
from kindly_web_search_mcp_server.rerank.conditional_bi import ConditionalBiOutcome
from kindly_web_search_mcp_server.rerank.diversity_stage import DiversityStageOutcome
from kindly_web_search_mcp_server.rerank.stage_runner import RankedStageOutcome


def _make_candidates(n: int) -> list[WebSearchResult]:
    return [
        WebSearchResult(
            title=f"doc{index}",
            link=f"https://example.com/c{index}",
            snippet=f"snippet {index}",
            score=0.5,
            hybrid_rrf_score=0.5,
        )
        for index in range(n)
    ]


class TestRerankCore(unittest.IsolatedAsyncioTestCase):
    async def test_monotone_funnel_success(self) -> None:
        base = _make_candidates(40)
        bi_outcome = ConditionalBiOutcome(
            candidates=base,
            embedding_context=None,
            duration_seconds=0.01,
            status="applied",
        )
        cross_order = base[:30]
        cross_outcome = RankedStageOutcome(
            candidates=cross_order,
            provider="cohere_fast",
            model="rerank-v4.0-fast",
            stage_name="cross_encoder",
            input_count=40,
            output_count=30,
            duration_seconds=0.01,
            relevance_scores=[0.9] * 30,
            max_score=0.9,
        )
        llm_order = base[:15]
        llm_outcome = RankedStageOutcome(
            candidates=llm_order,
            provider="openrouter",
            model="nvidia/nemotron-3-nano-30b-a3b:free",
            stage_name="rankllm",
            input_count=30,
            output_count=15,
            duration_seconds=0.01,
            relevance_scores=[],
            max_score=0.0,
            input_tokens=100,
            output_tokens=50,
        )

        with (
            patch.object(
                core, "run_conditional_bi_encoder", AsyncMock(return_value=bi_outcome)
            ) as mock_bi,
            patch.object(
                core, "run_cross_encoder_stage", AsyncMock(return_value=cross_outcome)
            ) as mock_cross,
            patch.object(core, "run_llm_stage", AsyncMock(return_value=llm_outcome)) as mock_llm,
        ):
            result = await core.rerank_results(
                "How do bridges stay standing?",
                base,
                research_goal="Find primary engineering evidence",
                query_type_hint="general",
            )

        assert result is not None
        self.assertEqual(len(result.results), 15)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model, "nvidia/nemotron-3-nano-30b-a3b:free")
        self.assertEqual(result.funnel_counts["input_count"], 40)
        self.assertEqual(result.funnel_counts["bi_output_count"], 40)
        self.assertEqual(result.funnel_counts["cross_output_count"], 30)
        self.assertEqual(result.funnel_counts["rankllm_output_count"], 15)
        self.assertEqual(len(result.stage_summaries), 3)

    async def test_blank_research_goal_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "research_goal must be non-blank"):
            await core.rerank_results("query", _make_candidates(2), research_goal="  ")

    async def test_empty_candidates_returns_immediately(self) -> None:
        result = await core.rerank_results("query", [], research_goal="goal")
        self.assertEqual(result.results, [])
        self.assertIsNone(result.provider)


if __name__ == "__main__":
    unittest.main()
