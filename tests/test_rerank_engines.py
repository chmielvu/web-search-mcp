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
        self.assertEqual(payload["query"], "Prefer official docs.\n\nsite reliability docs")
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
        self.assertEqual(payload["query"], "Prefer official docs.\n\nsite reliability docs")
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
