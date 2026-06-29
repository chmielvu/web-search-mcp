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
        self.assertEqual(get_rerank_engine("cohere_fast").engine_id, "cohere_fast")
        self.assertEqual(
            get_rerank_engine("cohere_fast_openrouter").engine_id,
            "cohere_fast_openrouter",
        )
        self.assertEqual(
            get_rerank_engine("local_baseline").engine_id, "local_baseline"
        )

    def test_cohere_fast_defaults_to_cohere_model(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import get_default_model

        self.assertEqual(get_default_model("cohere_fast"), "rerank-v4.0-fast")
        self.assertEqual(
            get_default_model("cohere_fast_openrouter"), "cohere/rerank-4-fast"
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
                "kindly_web_search_mcp_server.rerank.engines.cohere_rerank",
                new_callable=AsyncMock,
            ) as cohere,
            patch(
                "kindly_web_search_mcp_server.rerank.engines.openrouter_cohere_rerank",
                new_callable=AsyncMock,
            ) as openrouter,
        ):
            voyage.side_effect = RuntimeError("voyage down")
            cohere.side_effect = RuntimeError("cohere down")
            openrouter.side_effect = RuntimeError("openrouter down")

            result = await rerank_with_engine_fallback(
                query="query",
                candidates=candidates,
                engine_id="voyage",
            )

        self.assertEqual(result.engine_id, "none")
        self.assertEqual(result.ranked, [])
        self.assertEqual(result.ordered_candidates, candidates)

    async def test_cohere_fast_falls_back_to_openrouter_then_voyage(self) -> None:
        from kindly_web_search_mcp_server.rerank.engines import (
            rerank_with_engine_fallback,
        )

        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]

        with (
            patch(
                "kindly_web_search_mcp_server.rerank.engines.cohere_rerank",
                new_callable=AsyncMock,
            ) as cohere,
            patch(
                "kindly_web_search_mcp_server.rerank.engines.openrouter_cohere_rerank",
                new_callable=AsyncMock,
            ) as openrouter,
            patch(
                "kindly_web_search_mcp_server.rerank.engines.voyage_rerank",
                new_callable=AsyncMock,
            ) as voyage,
        ):
            cohere.side_effect = RuntimeError("cohere down")
            openrouter.side_effect = RuntimeError("openrouter down")
            voyage.return_value = [(1, 0.9), (0, 0.8)]

            result = await rerank_with_engine_fallback(
                query="query",
                candidates=candidates,
                engine_id="cohere_fast",
            )

        self.assertEqual(result.engine_id, "voyage")
        self.assertEqual([item.index for item in result.ranked], [1, 0])
        self.assertEqual([item.title for item in result.ordered_candidates], ["B", "A"])

    async def test_cohere_rerank_uses_request_payload(self) -> None:
        from kindly_web_search_mcp_server.rerank.cohere import cohere_rerank

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.5},
            ]
        }
        mock_client = SimpleNamespace(post=AsyncMock(return_value=response))

        ranked = await cohere_rerank(
            "site reliability docs",
            ["doc a", "doc b"],
            api_key="cohere-test-key",
            model="rerank-v4.0-fast",
            instruction="Prefer official docs.",
            http_client=mock_client,
            base_url="https://api.cohere.com/v2/rerank",
        )

        self.assertEqual(ranked, [(1, 0.95), (0, 0.5)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "rerank-v4.0-fast")
        self.assertEqual(
            payload["query"], "Prefer official docs.\n\nsite reliability docs"
        )
        self.assertEqual(payload["documents"], ["doc a", "doc b"])
        self.assertEqual(payload["top_n"], 2)
        self.assertEqual(
            mock_client.post.await_args.kwargs["headers"]["Authorization"],
            "Bearer cohere-test-key",
        )

    async def test_openrouter_cohere_rerank_uses_request_payload(self) -> None:
        from kindly_web_search_mcp_server.rerank.openrouter import (
            openrouter_cohere_rerank,
        )

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.93},
                {"index": 0, "relevance_score": 0.4},
            ]
        }
        mock_client = SimpleNamespace(post=AsyncMock(return_value=response))

        ranked = await openrouter_cohere_rerank(
            "site reliability docs",
            ["doc a", "doc b"],
            api_key="openrouter-test-key",
            model="cohere/rerank-4-fast",
            instruction="Prefer official docs.",
            http_client=mock_client,
            base_url="https://openrouter.ai/api/v1/rerank",
        )

        self.assertEqual(ranked, [(1, 0.93), (0, 0.4)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "cohere/rerank-4-fast")
        self.assertEqual(
            payload["query"], "Prefer official docs.\n\nsite reliability docs"
        )
        self.assertEqual(payload["documents"], ["doc a", "doc b"])
        self.assertEqual(payload["top_n"], 2)
        self.assertEqual(
            mock_client.post.await_args.kwargs["headers"]["Authorization"],
            "Bearer openrouter-test-key",
        )
        self.assertEqual(
            mock_client.post.await_args.kwargs["headers"]["Content-Type"],
            "application/json",
        )

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
