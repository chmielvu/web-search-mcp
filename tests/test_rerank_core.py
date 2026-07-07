from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.rerank import core
from kindly_web_search_mcp_server.rerank.stage_runner import RankedStageOutcome
from kindly_web_search_mcp_server.rerank.stages import DiversityStageOutcome


def _make_candidates(n: int) -> list[WebSearchResult]:
    return [
        WebSearchResult(
            title=f"doc{i}",
            link=f"https://example.com/c{i}",
            snippet=f"snippet {i}",
            score=0.5,
        )
        for i in range(n)
    ]


def _scored(candidates: list[WebSearchResult], scores: list[float]) -> list[WebSearchResult]:
    return [c.model_copy(update={"score": s}) for c, s in zip(candidates, scores)]


class TestRerankFusionAndGate(unittest.IsolatedAsyncioTestCase):
    """Validate score fusion (cross-encoder + LLM) and the relevance gate.

    These directly exercise the fix for unrelated documents surviving rerank:
    the cross-encoder real relevance and the LLM ordinal are fused into one
    score, and the gate rejects low-fused candidates before the top_k slice.
    """

    async def _run(self, stack_mode: str, top_k: int, threshold: float, alpha: float):
        base = _make_candidates(6)
        # Cross-encoder: real relevance. c4/c5 are "unrelated" (very low).
        ce_scores = [0.9, 0.8, 0.5, 0.4, 0.05, 0.02]
        # LLM ordinal (minmax of exp(-0.3*rank)); also ranks c4/c5 last.
        llm_scores = [1.0, 0.9, 0.7, 0.6, 0.2, 0.1]

        cross_outcome = RankedStageOutcome(
            candidates=_scored(base, ce_scores),
            provider="cohere_fast",
            model="rerank-v4.0-fast",
            stage_name="cohere_fast",
            input_count=6,
            output_count=6,
            duration_seconds=0.1,
            relevance_scores=ce_scores,
            max_score=0.9,
        )
        llm_outcome = RankedStageOutcome(
            candidates=_scored(base, llm_scores),
            provider="gpt-oss-worker",
            model="gpt-oss-120b",
            stage_name="llm_rerank",
            input_count=6,
            output_count=6,
            duration_seconds=0.1,
            relevance_scores=llm_scores,
            max_score=1.0,
        )

        async def fake_diversity(**kwargs):
            cands = kwargs["candidates"]
            return DiversityStageOutcome(
                candidates=cands,
                input_count=len(cands),
                output_count=len(cands),
                duration_seconds=0.1,
                removed_count=0,
            )

        mock_cross = AsyncMock(return_value=cross_outcome)
        mock_llm = AsyncMock(return_value=llm_outcome)
        mock_diversity = AsyncMock(side_effect=fake_diversity)
        mock_embed = AsyncMock(return_value=[0.0] * 64)

        with (
            patch.object(core.settings, "rerank_stack_mode", stack_mode),
            patch.object(core.settings, "rerank_score_threshold", threshold),
            patch.object(core.settings, "rerank_fusion_alpha", alpha),
            patch("kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage", mock_cross),
            patch("kindly_web_search_mcp_server.rerank.core.run_llm_stage", mock_llm),
            patch("kindly_web_search_mcp_server.rerank.core.run_diversity_pruning", mock_diversity),
            patch("kindly_web_search_mcp_server.rerank.core.embed_query", mock_embed),
        ):
            result = await core.rerank_results(
                "How do bridges stay standing?",
                base,
                top_k=top_k,
                query_type_hint="general",
            )
        return result

    async def test_bi_cross_llm_fusion_blends_and_gate_removes_unrelated(self) -> None:
        result = await self._run("bi_cross_llm", top_k=3, threshold=0.15, alpha=0.7)
        links = [r.link for r in result.results]
        # Unrelated documents (c4, c5) must NOT survive the fused gate.
        self.assertNotIn("https://example.com/c4", links)
        self.assertNotIn("https://example.com/c5", links)
        # Fused = 0.7*ce + 0.3*llm: c0 -> 0.93, c1 -> 0.83, c2 -> 0.56 (top 3).
        self.assertEqual(
            links,
            [
                "https://example.com/c0",
                "https://example.com/c1",
                "https://example.com/c2",
            ],
        )
        self.assertAlmostEqual(result.results[0].score, 0.93, places=4)
        self.assertAlmostEqual(result.results[1].score, 0.83, places=4)

    async def test_bi_llm_mode_gate_uses_llm_ordinal(self) -> None:
        # No cross-encoder: gate operates on LLM ordinal only; c5 (0.1) < 0.15 dropped.
        result = await self._run("bi_llm", top_k=3, threshold=0.15, alpha=0.7)
        links = [r.link for r in result.results]
        self.assertNotIn("https://example.com/c5", links)
        self.assertEqual(
            links,
            [
                "https://example.com/c0",
                "https://example.com/c1",
                "https://example.com/c2",
            ],
        )


class TestRerankCore(unittest.IsolatedAsyncioTestCase):
    async def test_short_circuit_rerank_records_bypass_metric(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                score=0.5,
            )
        ]

        with patch(
            "kindly_web_search_mcp_server.rerank.core.record_rerank_stage",
        ) as mock_record_stage:
            reranked = await rerank_results(
                "example query",
                candidates,
                top_k=10,
                research_goal="Find authoritative docs for the deployment flow",
                query_type_hint="comparison",
            )

        self.assertEqual(reranked.results, candidates)
        mock_record_stage.assert_called_once_with(
            stage="bypass",
            input_count=1,
            output_count=1,
            duration_seconds=0.0,
        )


if __name__ == "__main__":
    unittest.main()
