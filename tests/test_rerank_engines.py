from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.inference.types import ModelCapability, ModelSpec
from kindly_web_search_mcp_server.models import WebSearchResult


def _spec(provider: str, model: str, base_url: str, api_key_env: str) -> ModelSpec:
    return ModelSpec(
        spec_id=f"{provider}-test",
        provider=provider,
        model_id=model,
        base_url=base_url,
        api_key_env=api_key_env,
        capabilities=frozenset({ModelCapability.RERANK}),
        default_timeout=5.0,
    )


class _FakeAsyncClient:
    def __init__(self, response: MagicMock) -> None:
        self.response = response
        self.post_calls: list[dict[str, object]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> MagicMock:
        self.post_calls.append({"url": url, **kwargs})
        return self.response


class TestRerankEngines(unittest.IsolatedAsyncioTestCase):
    def test_rerank_models_describe_candidate_and_result_contract(self) -> None:
        from kindly_web_search_mcp_server.rerank.models import RerankCandidate, RerankResult

        candidate = RerankCandidate(index=2, document="Title: C")
        result = RerankResult(index=2, relevance_score=0.87)

        self.assertEqual(candidate.index, 2)
        self.assertEqual(candidate.document, "Title: C")
        self.assertEqual(result.index, 2)
        self.assertEqual(result.relevance_score, 0.87)

    async def test_cohere_rerank_uses_request_payload(self) -> None:
        from kindly_web_search_mcp_server.inference.adapters.cohere import execute_cohere_rerank
        from kindly_web_search_mcp_server.rerank.providers import parse_rerank_response

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.5},
            ]
        }
        client = _FakeAsyncClient(response)
        spec = _spec(
            "cohere",
            "rerank-v4.0-fast",
            "https://api.cohere.com/v2/rerank",
            "COHERE_API_KEY",
        )

        with patch.dict(os.environ, {"COHERE_API_KEY": "cohere-test-key"}):
            with patch("httpx.AsyncClient", return_value=client):
                generation = await execute_cohere_rerank(
                    spec,
                    query="site reliability docs",
                    documents=["doc a", "doc b"],
                    top_n=2,
                )

        ranked = parse_rerank_response(generation, candidate_count=2)
        self.assertEqual(
            [(item.index, item.relevance_score) for item in ranked],
            [(1, 0.95), (0, 0.5)],
        )
        payload = client.post_calls[0]
        self.assertEqual(payload["url"], "https://api.cohere.com/v2/rerank")
        body = payload["json"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "rerank-v4.0-fast")
        self.assertEqual(body["query"], "site reliability docs")
        self.assertEqual(body["documents"], ["doc a", "doc b"])
        self.assertEqual(body["top_n"], 2)
        headers = payload["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer cohere-test-key")

    async def test_openrouter_cohere_rerank_uses_request_payload(self) -> None:
        from kindly_web_search_mcp_server.inference.adapters.openrouter import (
            execute_openrouter_rerank,
        )
        from kindly_web_search_mcp_server.rerank.providers import parse_rerank_response

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.93},
                {"index": 0, "relevance_score": 0.4},
            ]
        }
        client = _FakeAsyncClient(response)
        spec = _spec(
            "openrouter_rerank",
            "cohere/rerank-4-fast",
            "https://openrouter.ai/api/v1/rerank",
            "OPENROUTER_API_KEY",
        )

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "openrouter-test-key"}):
            with patch("httpx.AsyncClient", return_value=client):
                generation = await execute_openrouter_rerank(
                    spec,
                    query="site reliability docs",
                    documents=["doc a", "doc b"],
                    top_n=2,
                )

        ranked = parse_rerank_response(generation, candidate_count=2)
        self.assertEqual(
            [(item.index, item.relevance_score) for item in ranked],
            [(1, 0.93), (0, 0.4)],
        )
        payload = client.post_calls[0]
        self.assertEqual(payload["url"], "https://openrouter.ai/api/v1/rerank")
        body = payload["json"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "cohere/rerank-4-fast")
        self.assertEqual(body["query"], "site reliability docs")
        self.assertEqual(body["documents"], ["doc a", "doc b"])
        self.assertEqual(body["top_n"], 2)
        headers = payload["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], "Bearer openrouter-test-key")

    async def test_voyage_rerank_sends_exact_query(self) -> None:
        from kindly_web_search_mcp_server.inference.adapters.voyage import execute_voyage_rerank
        from kindly_web_search_mcp_server.rerank.providers import parse_rerank_response

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.8},
            ]
        }
        client = _FakeAsyncClient(response)
        spec = _spec(
            "voyage",
            "rerank-2.5",
            "https://api.voyageai.com/v1/rerank",
            "VOYAGE_API_KEY",
        )

        with patch.dict(os.environ, {"VOYAGE_API_KEY": "voyage-test-key"}):
            with patch("httpx.AsyncClient", return_value=client):
                generation = await execute_voyage_rerank(
                    spec,
                    query="site reliability docs",
                    documents=["doc a", "doc b"],
                    top_n=2,
                )

        ranked = parse_rerank_response(generation, candidate_count=2)
        self.assertEqual(
            [(item.index, item.relevance_score) for item in ranked],
            [(1, 0.9), (0, 0.8)],
        )
        payload = client.post_calls[0]
        self.assertEqual(payload["url"], "https://api.voyageai.com/v1/rerank")
        body = payload["json"]
        assert isinstance(body, dict)
        self.assertEqual(body["query"], "site reliability docs")
        self.assertEqual(body["top_k"], 2)

    def test_ordered_yaml_candidate_serialization_escapes_content(self) -> None:
        import yaml

        from kindly_web_search_mcp_server.rerank.providers import build_rerank_candidates

        candidate = WebSearchResult(
            title="A: title\nwith newline",
            link="https://example.com/a?x=1&y=2",
            snippet="Unicode π and YAML: [not, structure]",
            domain="example.com",
            providers=["cohere", "brave"],
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
        """The shared parser accepts a valid partial provider ranking."""
        from kindly_web_search_mcp_server.rerank.providers import parse_rerank_response

        partial = [{"index": 1, "relevance_score": 0.91}]
        ranked = parse_rerank_response(partial, candidate_count=3)
        self.assertEqual(
            [(item.index, item.relevance_score) for item in ranked],
            [(1, 0.91)],
        )

    def test_rerank_parser_rejects_out_of_range_scores(self) -> None:
        from kindly_web_search_mcp_server.rerank.providers import (
            RerankResponseError,
            parse_rerank_response,
        )

        for score in (-0.1, 1.1):
            with self.subTest(score=score):
                with self.assertRaises(RerankResponseError):
                    parse_rerank_response(
                        [{"index": 0, "relevance_score": score}],
                        candidate_count=2,
                    )

    def test_rerank_parsers_reject_invalid_payloads(self) -> None:
        from kindly_web_search_mcp_server.rerank.providers import (
            RerankResponseError,
            parse_rerank_response,
        )

        invalid_payloads = [
            [],
            "not-a-list",
            [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}],
            [{"index": 0, "relevance_score": 0.5}, {"index": 2, "relevance_score": 0.4}],
            [{"index": 0, "relevance_score": float("nan")}],
            [{"index": 0, "relevance_score": 0.5}, {"index": 1}],
            [
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.4},
                {"index": 2, "relevance_score": 0.3},
            ],
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(RerankResponseError):
                    parse_rerank_response(payload, candidate_count=2)


if __name__ == "__main__":
    unittest.main()
