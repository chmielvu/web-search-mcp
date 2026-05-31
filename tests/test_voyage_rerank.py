from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


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

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return _FakeResponse(self.payload)


class TestVoyageRerank(unittest.IsolatedAsyncioTestCase):
    def test_default_voyage_reranker_model_is_25(self) -> None:
        from kindly_web_search_mcp_server.settings import Settings

        self.assertEqual(Settings().rerank_provider, "voyage")
        self.assertEqual(Settings().voyage_rerank_model, "rerank-2.5")

    async def test_voyage_rerank_uses_primary_model_and_top_k(self) -> None:
        from kindly_web_search_mcp_server.rerank.voyage import voyage_rerank

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

        ranked = await voyage_rerank(
            "same text ranking",
            ["duplicate document", "duplicate document"],
            api_key="voyage-test-key",
            top_n=2,
            http_client=client,
        )

        self.assertEqual(ranked, [(1, 0.91), (0, 0.22)])
        self.assertEqual(client.post_calls[0]["url"], "https://api.voyageai.com/v1/rerank")
        self.assertEqual(client.post_calls[0]["headers"]["Authorization"], "Bearer voyage-test-key")
        self.assertEqual(client.post_calls[0]["json"]["model"], "rerank-2.5")
        self.assertEqual(client.post_calls[0]["json"]["top_k"], 2)
        self.assertTrue(client.post_calls[0]["json"]["truncation"])
        self.assertFalse(client.post_calls[0]["json"]["return_documents"])


if __name__ == "__main__":
    unittest.main()
