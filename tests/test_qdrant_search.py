from __future__ import annotations

import sys
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestQdrantSearch(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        from kindly_web_search_mcp_server.search.providers import qdrant as qdrant_module

        tasks = list(qdrant_module._EMBEDDING_INFLIGHT.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        qdrant_module._EMBEDDING_INFLIGHT.clear()
        qdrant_module._EMBEDDING_CACHE.clear()

    async def test_qdrant_embedding_timeout_cancels_inflight_task(self) -> None:
        from kindly_web_search_mcp_server.search.providers import qdrant as qdrant_module

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_embed_query(_: str) -> list[float]:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return [0.1, 0.2]

        fake_embedder = SimpleNamespace(embed_query=slow_embed_query)

        with patch.object(qdrant_module, "_EMBEDDER", fake_embedder):
            with self.assertRaises(asyncio.TimeoutError):
                await qdrant_module._embed_qdrant_query("slow query", deadline=0.01)

        self.assertTrue(started.is_set())
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        self.assertEqual(qdrant_module._EMBEDDING_INFLIGHT, {})

    async def test_search_qdrant_uses_hf_auth_token(self) -> None:
        from kindly_web_search_mcp_server.search.providers.qdrant import search_qdrant
        from kindly_web_search_mcp_server.search.providers import qdrant as qdrant_module

        captured: dict[str, object] = {}

        class FakeClient:
            async def query_points(self, **kwargs) -> object:
                captured["query_points"] = kwargs
                return SimpleNamespace(
                    points=[
                        SimpleNamespace(
                            payload={
                                "url": "https://example.com",
                                "title": "Title",
                                "snippet": "Snippet",
                                "domain": "example.com",
                            },
                            score=0.99,
                        )
                    ]
                )

            async def close(self) -> None:
                return None

        def _client_factory(**kwargs: object) -> FakeClient:
            captured["client_kwargs"] = kwargs
            return FakeClient()

        with (
            patch.object(
                qdrant_module.settings,
                "qdrant_space_url",
                "https://chmielvu-web-index.hf.space",
            ),
            patch.object(qdrant_module.settings, "hf_token", "hf-test-token"),
            patch.object(
                qdrant_module,
                "_EMBEDDER",
                SimpleNamespace(embed_query=AsyncMock(return_value=[0.1, 0.2])),
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.qdrant.encode_bm25",
                return_value={"indices": [1], "values": [1.0]},
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.qdrant.AsyncQdrantClient",
                side_effect=_client_factory,
            ),
        ):
            results = await search_qdrant("hello", num_results=1)

        self.assertEqual(captured["client_kwargs"]["url"], "https://chmielvu-web-index.hf.space")
        self.assertEqual(captured["client_kwargs"]["auth_token_provider"](), "hf-test-token")
        self.assertEqual(captured["query_points"]["collection_name"], "web_results")
        self.assertEqual(results[0].link, "https://example.com")

    async def test_ensure_collection_raises_on_unexpected_failure(self) -> None:
        from kindly_web_search_mcp_server.index.web_results_index import WebResultsIndex

        class FakeClient:
            async def get_collection(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError("not found")

            async def create_collection(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError("boom")

        index = WebResultsIndex(
            url="https://example.com", auth_token_provider=lambda: "hf-test-token"
        )

        with patch.object(index, "_ensure_client", return_value=FakeClient()):
            with self.assertRaises(RuntimeError):
                await index._ensure_collection()

        self.assertFalse(index._collection_ok)


if __name__ == "__main__":
    unittest.main()
