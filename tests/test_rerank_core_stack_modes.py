from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


def _candidate(name: str) -> WebSearchResult:
    return WebSearchResult(
        title=name,
        link=f"https://example.com/{name.lower()}",
        snippet=f"snippet {name}",
        score=1.0,
    )


class TestRerankCoreStackModes(unittest.IsolatedAsyncioTestCase):
    async def test_bi_encoder_runs_for_normal_overfetch_windows_by_default(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [_candidate(f"Item{i}") for i in range(30)]

        with (
            patch("kindly_web_search_mcp_server.rerank.core.settings") as s,
            patch("kindly_web_search_mcp_server.rerank.core.decide_rerank") as mock_decide,
            patch(
                "kindly_web_search_mcp_server.rerank.core.bi_encoder_filter",
                new_callable=AsyncMock,
            ) as mock_bi_encoder,
            patch(
                "kindly_web_search_mcp_server.rerank.core.record_rerank_candidate_rows_async",
                new_callable=AsyncMock,
            ),
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage",
                new_callable=AsyncMock,
            ) as mock_provider,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_llm_stage",
                new_callable=AsyncMock,
            ) as mock_llm,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_diversity_pruning",
                new_callable=AsyncMock,
            ) as mock_diversity,
            patch("kindly_web_search_mcp_server.rerank.core.emit_observability_event"),
        ):
            s.rerank_stack_mode = "bi_cross_llm"
            s.rerank_bi_encoder_min_candidates = 0
            s.rerank_provider = "voyage"
            s.rerank_score_threshold = -999.0
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False
            mock_decide.return_value = MagicMock(
                should_rerank=True,
                reason="eligible",
                query_type="general",
                candidate_count=len(candidates),
            )
            mock_bi_encoder.return_value = (candidates, None)
            mock_provider.return_value = SimpleNamespace(
                candidates=candidates,
                provider="cohere_fast",
                model="rerank-v4.0-fast",
                stage_name="cohere_fast",
                input_count=len(candidates),
                output_count=len(candidates),
                duration_seconds=0.1,
                relevance_scores=[0.9],
                max_score=0.9,
                error=None,
            )
            mock_llm.return_value = SimpleNamespace(
                candidates=candidates,
                provider="vercel",
                model="openai/gpt-oss-20b",
                stage_name="llm_rerank",
                input_count=len(candidates),
                output_count=20,
                duration_seconds=0.1,
                relevance_scores=[0.95],
                max_score=0.95,
                error=None,
            )
            mock_diversity.return_value = SimpleNamespace(
                candidates=candidates,
                input_count=20,
                output_count=20,
                duration_seconds=0.1,
                removed_count=0,
            )

            await rerank_results(
                "query",
                candidates,
                top_k=10,
                precomputed_embedding=[0.1, 0.2, 0.3],
            )

        mock_bi_encoder.assert_awaited_once()

    async def test_bi_cross_mode_uses_cross_encoder_stage_only(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        with (
            patch("kindly_web_search_mcp_server.rerank.core.settings") as s,
            patch("kindly_web_search_mcp_server.rerank.core.decide_rerank") as mock_decide,
            patch(
                "kindly_web_search_mcp_server.rerank.core.embed_query", new_callable=AsyncMock
            ) as mock_embed_query,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage",
                new_callable=AsyncMock,
            ) as mock_provider,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_llm_stage",
                new_callable=AsyncMock,
                create=True,
            ) as mock_llm,
            patch("kindly_web_search_mcp_server.rerank.core.emit_observability_event"),
        ):
            s.rerank_stack_mode = "bi_cross"
            s.rerank_provider = "voyage"
            s.rerank_score_threshold = -999.0
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False
            mock_decide.return_value = MagicMock(
                should_rerank=True,
                reason="eligible",
                query_type="general",
                candidate_count=3,
            )
            mock_embed_query.side_effect = Exception("skip embeddings")
            mock_provider.return_value = SimpleNamespace(
                **{
                    "candidates": [candidates[1], candidates[0]],
                    "provider": "voyage",
                    "model": "rerank-2.5",
                    "stage_name": "voyage",
                    "input_count": 3,
                    "output_count": 2,
                    "duration_seconds": 0.1,
                    "relevance_scores": [0.9, 0.8],
                    "max_score": 0.9,
                    "error": None,
                }
            )
            mock_llm.return_value = SimpleNamespace(
                candidates=candidates,
                provider="cerebras",
                model="cerebras/openai/gpt-oss-120b",
                stage_name="llm_rerank",
                input_count=2,
                output_count=2,
                duration_seconds=0.1,
                relevance_scores=[],
                max_score=0.0,
                error=None,
            )

            result = await rerank_results("query", candidates, top_k=2)

        self.assertTrue(mock_provider.await_count)
        self.assertFalse(mock_llm.await_count)
        self.assertEqual([item.title for item in result.results], ["B", "A"])

    async def test_bi_llm_mode_uses_llm_stage_only(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        with (
            patch("kindly_web_search_mcp_server.rerank.core.settings") as s,
            patch("kindly_web_search_mcp_server.rerank.core.decide_rerank") as mock_decide,
            patch(
                "kindly_web_search_mcp_server.rerank.core.embed_query", new_callable=AsyncMock
            ) as mock_embed_query,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage",
                new_callable=AsyncMock,
            ) as mock_provider,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_llm_stage",
                new_callable=AsyncMock,
                create=True,
            ) as mock_llm,
            patch("kindly_web_search_mcp_server.rerank.core.emit_observability_event"),
        ):
            s.rerank_stack_mode = "bi_llm"
            s.rerank_provider = "voyage"
            s.rerank_score_threshold = -999.0
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False
            mock_decide.return_value = MagicMock(
                should_rerank=True,
                reason="eligible",
                query_type="general",
                candidate_count=3,
            )
            mock_embed_query.side_effect = Exception("skip embeddings")
            mock_provider.return_value = SimpleNamespace(
                candidates=candidates,
                provider="none",
                model=None,
                stage_name="none",
                input_count=3,
                output_count=3,
                duration_seconds=0.1,
                relevance_scores=[],
                max_score=0.0,
                error=None,
            )
            mock_llm.return_value = SimpleNamespace(
                candidates=[candidates[2], candidates[0]],
                provider="cerebras",
                model="cerebras/openai/gpt-oss-120b",
                stage_name="llm_rerank",
                input_count=3,
                output_count=2,
                duration_seconds=0.1,
                relevance_scores=[0.95, 0.9],
                max_score=0.95,
                error=None,
            )

            result = await rerank_results("query", candidates, top_k=2)

        self.assertFalse(mock_provider.await_count)
        self.assertTrue(mock_llm.await_count)
        self.assertEqual([item.title for item in result.results], ["C", "A"])

    async def test_bi_cross_llm_mode_runs_both_stages_in_order(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]
        call_order: list[str] = []

        async def _provider(*args, **kwargs):
            call_order.append("provider")
            return SimpleNamespace(
                candidates=[candidates[1], candidates[0]],
                provider="voyage",
                model="rerank-2.5",
                stage_name="voyage",
                input_count=3,
                output_count=2,
                duration_seconds=0.1,
                relevance_scores=[0.9, 0.8],
                max_score=0.9,
                error=None,
            )

        async def _llm(*args, **kwargs):
            call_order.append("llm")
            return SimpleNamespace(
                candidates=[candidates[1], candidates[0]],
                provider="cerebras",
                model="cerebras/openai/gpt-oss-120b",
                stage_name="llm_rerank",
                input_count=2,
                output_count=2,
                duration_seconds=0.1,
                relevance_scores=[0.97, 0.93],
                max_score=0.97,
                error=None,
            )

        with (
            patch("kindly_web_search_mcp_server.rerank.core.settings") as s,
            patch("kindly_web_search_mcp_server.rerank.core.decide_rerank") as mock_decide,
            patch(
                "kindly_web_search_mcp_server.rerank.core.embed_query", new_callable=AsyncMock
            ) as mock_embed_query,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_cross_encoder_stage",
                new_callable=AsyncMock,
            ) as mock_provider,
            patch(
                "kindly_web_search_mcp_server.rerank.core.run_llm_stage",
                new_callable=AsyncMock,
                create=True,
            ) as mock_llm,
            patch("kindly_web_search_mcp_server.rerank.core.emit_observability_event"),
        ):
            s.rerank_stack_mode = "bi_cross_llm"
            s.rerank_provider = "voyage"
            s.rerank_score_threshold = -999.0
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False
            mock_decide.return_value = MagicMock(
                should_rerank=True,
                reason="eligible",
                query_type="general",
                candidate_count=3,
            )
            mock_embed_query.side_effect = Exception("skip embeddings")
            mock_provider.side_effect = _provider
            mock_llm.side_effect = _llm

            result = await rerank_results("query", candidates, top_k=2)

        self.assertEqual(call_order, ["provider", "llm"])
        self.assertEqual([item.title for item in result.results], ["B", "A"])
