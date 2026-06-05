from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


def _candidate(title: str, link: str = "https://example.com") -> WebSearchResult:
    return WebSearchResult(title=title, link=f"{link}/{title}", snippet=f"{title} body")


class TestRerankEngines(unittest.IsolatedAsyncioTestCase):
    def test_rerank_models_describe_candidate_and_result_contract(self) -> None:
        from kindly_web_search_mcp_server.rerank.models import (
            RerankCandidate,
            RerankResult,
        )

        candidate = RerankCandidate(index=2, document="Title: C")
        result = RerankResult(index=2, score=0.87)

        self.assertEqual(candidate.index, 2)
        self.assertEqual(candidate.document, "Title: C")
        self.assertEqual(result.index, 2)
        self.assertEqual(result.score, 0.87)

    def test_supported_engine_ids_are_registered(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import get_rerank_engine

        self.assertEqual(get_rerank_engine("none").engine_id, "none")
        self.assertEqual(get_rerank_engine("voyage").engine_id, "voyage")
        self.assertEqual(get_rerank_engine("jina").engine_id, "jina")
        self.assertEqual(get_rerank_engine("gcp_cloudrun").engine_id, "gcp_cloudrun")
        self.assertEqual(
            get_rerank_engine("local_baseline").engine_id, "local_baseline"
        )

    async def test_none_engine_preserves_merged_order(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import (
            rerank_with_engine_fallback,
        )

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        result = await rerank_with_engine_fallback(
            query="query",
            candidates=candidates,
            engine_id="none",
        )

        self.assertEqual(result.engine_id, "none")
        self.assertIsNone(result.model)
        self.assertEqual(result.ranked, [])
        self.assertEqual(result.ordered_candidates, candidates)

    async def test_all_provider_failures_preserve_merged_order(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import (
            rerank_with_engine_fallback,
        )

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        with (
            patch(
                "kindly_web_search_mcp_server.rerank.engines.voyage_rerank",
                new_callable=AsyncMock,
            ) as voyage,
            patch(
                "kindly_web_search_mcp_server.rerank.engines.jina_rerank",
                new_callable=AsyncMock,
            ) as jina,
        ):
            voyage.side_effect = RuntimeError("voyage down")
            jina.side_effect = RuntimeError("jina down")

            result = await rerank_with_engine_fallback(
                query="query",
                candidates=candidates,
                engine_id="voyage",
            )

        self.assertEqual(result.engine_id, "none")
        self.assertEqual(result.ranked, [])
        self.assertEqual(result.ordered_candidates, candidates)

    async def test_voyage_engine_uses_existing_provider_client(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import (
            rerank_with_engine_fallback,
        )

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        with patch(
            "kindly_web_search_mcp_server.rerank.engines.voyage_rerank",
            new_callable=AsyncMock,
        ) as voyage:
            voyage.return_value = [(2, 0.9), (0, 0.7)]

            result = await rerank_with_engine_fallback(
                query="query",
                candidates=candidates,
                engine_id="voyage",
                model="rerank-2.5",
            )

        self.assertEqual(result.engine_id, "voyage")
        self.assertEqual(result.model, "rerank-2.5")
        self.assertEqual([item.index for item in result.ranked], [2, 0])
        self.assertEqual([item.title for item in result.ordered_candidates], ["C", "A"])

    async def test_voyage_rerank_prepends_instruction_to_query(self) -> None:
        from kindly_web_search_mcp_server.rerank.voyage import voyage_rerank

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.8},
            ]
        }
        mock_client = SimpleNamespace(post=AsyncMock(return_value=response))

        with patch(
            "kindly_web_search_mcp_server.rerank.voyage._get_voyage_client",
            return_value=mock_client,
        ):
            ranked = await voyage_rerank(
                "site reliability docs",
                ["doc a", "doc b"],
                api_key="voyage-test-key",
                instruction="Prioritize official docs and canonical references.",
            )

        self.assertEqual(ranked, [(1, 0.9), (0, 0.8)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(
            payload["query"],
            "Prioritize official docs and canonical references.\n\nsite reliability docs",
        )
        self.assertEqual(payload["top_k"], 2)


if __name__ == "__main__":
    unittest.main()
