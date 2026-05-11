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


class TestJinaRerank(unittest.IsolatedAsyncioTestCase):
    async def test_jina_rerank_uses_returned_indexes_for_duplicate_documents(self) -> None:
        from kindly_web_search_mcp_server.rerank.jina import jina_rerank

        client = _FakeClient(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.22},
                ]
            }
        )

        ranked = await jina_rerank(
            "same text ranking",
            ["duplicate document", "duplicate document"],
            api_key="jina-test-key",
            http_client=client,
        )

        self.assertEqual(ranked, [(1, 0.91), (0, 0.22)])
        self.assertEqual(client.post_calls[0]["url"], "https://api.jina.ai/v1/rerank")
        self.assertEqual(client.post_calls[0]["json"]["model"], "jina-reranker-v3")
        self.assertEqual(client.post_calls[0]["json"]["return_documents"], False)


if __name__ == "__main__":
    unittest.main()
