from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.inference.types import ModelCapability, ModelSpec


def _spec() -> ModelSpec:
    return ModelSpec(
        spec_id="voyage-test",
        provider="voyage",
        model_id="rerank-2.5",
        base_url="https://api.voyageai.com/v1/rerank",
        api_key_env="VOYAGE_API_KEY",
        capabilities=frozenset({ModelCapability.RERANK}),
        default_timeout=5.0,
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.post_calls: list[dict] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return _FakeResponse(self.payload)


class TestVoyageRerank(unittest.IsolatedAsyncioTestCase):
    def test_default_voyage_reranker_model_is_25(self) -> None:
        from kindly_web_search_mcp_server.settings import Settings

        self.assertEqual(Settings().voyage_rerank_model, "rerank-2.5")

    async def test_voyage_rerank_uses_primary_model_and_top_k(self) -> None:
        from kindly_web_search_mcp_server.inference.adapters.voyage import (
            execute_voyage_rerank,
        )
        from kindly_web_search_mcp_server.rerank.providers import parse_rerank_response

        client = _FakeClient(
            {
                "object": "list",
                "data": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.22},
                ],
                "model": "rerank-2.5",
                "usage": {"total_tokens": 8},
            }
        )

        with patch.dict(os.environ, {"VOYAGE_API_KEY": "voyage-test-key"}):
            with patch("httpx.AsyncClient", return_value=client):
                generation = await execute_voyage_rerank(
                    _spec(),
                    query="same text ranking",
                    documents=["duplicate document", "duplicate document"],
                    top_n=2,
                )

        ranked = parse_rerank_response(generation, candidate_count=2)
        self.assertEqual(
            [(item.index, item.relevance_score) for item in ranked], [(1, 0.91), (0, 0.22)]
        )
        self.assertEqual(client.post_calls[0]["url"], "https://api.voyageai.com/v1/rerank")
        self.assertEqual(client.post_calls[0]["headers"]["Authorization"], "Bearer voyage-test-key")
        self.assertEqual(client.post_calls[0]["json"]["model"], "rerank-2.5")
        self.assertEqual(client.post_calls[0]["json"]["top_k"], 2)

    async def test_voyage_rerank_prepends_instruction_text_when_provided(self) -> None:
        from kindly_web_search_mcp_server.inference.adapters.voyage import execute_voyage_rerank

        client = _FakeClient(
            {
                "object": "list",
                "data": [{"index": 0, "relevance_score": 0.5}],
                "model": "rerank-2.5",
                "usage": {"total_tokens": 4},
            }
        )

        with patch.dict(os.environ, {"VOYAGE_API_KEY": "voyage-test-key"}):
            with patch("httpx.AsyncClient", return_value=client):
                await execute_voyage_rerank(
                    _spec(),
                    query="base query",
                    documents=["doc 1"],
                    instruction="Prefer authoritative docs.",
                )

        self.assertEqual(
            client.post_calls[0]["json"]["query"],
            "Prefer authoritative docs.\n\nbase query",
        )

    def test_voyage_reformats_all_cross_segments_instruction_first(self) -> None:
        from kindly_web_search_mcp_server.prompts.rerank import build_cross_encoder_query
        from kindly_web_search_mcp_server.inference.adapters.voyage import _format_voyage_query

        compact = build_cross_encoder_query(
            "query",
            "social_media",
            "find discussion",
            reranking_instructions="Prefer the requested community.",
        )
        formatted = _format_voyage_query(compact)
        assert formatted.startswith(
            "Research goal: find discussion\n"
            "Intent: social_media\n"
            "Match the requested platform or community"
        )
        assert "Caller preference: Prefer the requested community." in formatted
        assert formatted.endswith("\n\nQuery: query")


if __name__ == "__main__":
    unittest.main()
