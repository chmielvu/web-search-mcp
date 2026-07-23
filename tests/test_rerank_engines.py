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
            http_client=mock_client,
            base_url="https://api.cohere.com/v2/rerank",
        )

        self.assertEqual(ranked, [(1, 0.95), (0, 0.5)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "rerank-v4.0-fast")
        self.assertEqual(payload["query"], "site reliability docs")
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
            http_client=mock_client,
            base_url="https://openrouter.ai/api/v1/rerank",
        )

        self.assertEqual(ranked, [(1, 0.93), (0, 0.4)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(payload["model"], "cohere/rerank-4-fast")
        self.assertEqual(payload["query"], "site reliability docs")
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

    async def test_voyage_rerank_sends_exact_query(self) -> None:
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
            )

        self.assertEqual(ranked, [(1, 0.9), (0, 0.8)])
        payload = mock_client.post.await_args.kwargs["json"]
        self.assertEqual(
            payload["query"],
            "site reliability docs",
        )
        self.assertEqual(payload["top_k"], 2)

    def test_ordered_yaml_candidate_serialization_escapes_content(self) -> None:
        import yaml

        from kindly_web_search_mcp_server.rerank.providers import build_rerank_candidates

        candidate = WebSearchResult(
            title="A: title\nwith newline",
            link="https://example.com/a?x=1&y=2",
            snippet="Unicode π and YAML: [not, structure]",
            domain="example.com",
            providers=["cohere", "brave"],
            provider_count=2,
        )
        document = build_rerank_candidates([candidate])[0].document
        self.assertEqual(
            [
                line.split(":", 1)[0]
                for line in document.splitlines()
                if line and not line.startswith((" ", "-"))
            ],
            ["Title", "Snippet", "URL", "Domain", "Providers", "ProviderCount"],
        )
        parsed = yaml.safe_load(document)
        self.assertEqual(parsed["Title"], candidate.title)
        self.assertEqual(parsed["Snippet"], candidate.snippet)
        self.assertEqual(parsed["Providers"], ["cohere", "brave"])

    def test_rerank_parsers_accept_partial_top_n_results(self) -> None:
        """top_n is a cap: both APIs may return fewer than document_count."""
        from kindly_web_search_mcp_server.rerank.cohere import (
            _parse_rerank_results as parse_cohere,
        )
        from kindly_web_search_mcp_server.rerank.openrouter import (
            _parse_rerank_results as parse_openrouter,
        )

        partial = {"results": [{"index": 1, "relevance_score": 0.91}]}
        for parser in (parse_cohere, parse_openrouter):
            with self.subTest(parser=parser.__module__):
                ranked = parser(partial, 3)
                self.assertEqual(ranked, [(1, 0.91)])

    def test_rerank_parsers_clamp_out_of_range_scores(self) -> None:
        from kindly_web_search_mcp_server.rerank.cohere import (
            _parse_rerank_results as parse_cohere,
        )
        from kindly_web_search_mcp_server.rerank.openrouter import (
            _parse_rerank_results as parse_openrouter,
        )

        payload = {
            "results": [
                {"index": 0, "relevance_score": -0.1},
                {"index": 1, "relevance_score": 1.1},
            ]
        }
        for parser in (parse_cohere, parse_openrouter):
            with self.subTest(parser=parser.__module__):
                ranked = parser(payload, 2)
                self.assertEqual(ranked, [(0, 0.0), (1, 1.0)])

    def test_rerank_parsers_reject_invalid_payloads(self) -> None:
        from kindly_web_search_mcp_server.rerank.cohere import (
            _parse_rerank_results as parse_cohere,
        )
        from kindly_web_search_mcp_server.rerank.openrouter import (
            _parse_rerank_results as parse_openrouter,
        )

        invalid_payloads = [
            {"results": []},  # empty when docs exist
            {"results": "not-a-list"},
            {
                "results": [
                    {"index": 0, "relevance_score": 0.5},
                    {"index": 0, "relevance_score": 0.4},  # duplicate
                ]
            },
            {
                "results": [
                    {"index": 0, "relevance_score": 0.5},
                    {"index": 2, "relevance_score": 0.4},  # OOB for count=2
                ]
            },
            {
                "results": [
                    {"index": 0, "relevance_score": float("nan")},
                    {"index": 1, "relevance_score": 0.4},
                ]
            },
            {
                # more results than documents
                "results": [
                    {"index": 0, "relevance_score": 0.5},
                    {"index": 1, "relevance_score": 0.4},
                    {"index": 2, "relevance_score": 0.3},
                ]
            },
        ]
        for parser in (parse_cohere, parse_openrouter):
            for payload in invalid_payloads:
                with self.subTest(parser=parser.__module__, payload=payload):
                    with self.assertRaises(ValueError):
                        parser(payload, 2)


if __name__ == "__main__":
    unittest.main()
