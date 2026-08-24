from __future__ import annotations

import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

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

        async def slow_embed_query(_: str, **_kwargs: object) -> list[float]:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return [0.1, 0.2]

        with patch.object(qdrant_module, "embed_query", slow_embed_query):
            with self.assertRaises(asyncio.TimeoutError):
                await qdrant_module._embed_qdrant_query("slow query", deadline=0.01)

        self.assertTrue(started.is_set())
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        self.assertEqual(qdrant_module._EMBEDDING_INFLIGHT, {})

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

    async def test_qdrant_index_and_search_roundtrip(self) -> None:
        from kindly_web_search_mcp_server.index.web_results_index import (
            WebResultsIndex,
            COLLECTION_NAME,
        )
        from kindly_web_search_mcp_server.search.providers.qdrant import search_qdrant
        from kindly_web_search_mcp_server.models import WebSearchResult
        from qdrant_client import AsyncQdrantClient

        shared_client = AsyncQdrantClient(location=":memory:")
        idx = WebResultsIndex(url="https://fake-url")

        async def get_client() -> AsyncQdrantClient:
            return shared_client

        idx._ensure_client = get_client

        item = WebSearchResult(
            title="FastAPI Lifespan Events",
            link="https://fastapi.tiangolo.com/advanced/events/",
            snippet="Learn how to handle lifespan events in FastAPI.",
            domain="fastapi.tiangolo.com",
            score=0.9,
        )
        dense = [[0.05] * 786]
        sparse = [{"indices": [1, 2, 3], "values": [0.5, 0.3, 0.8]}]

        await idx.index_results(
            results=[item],
            dense_embeddings=dense,
            sparse_embeddings=sparse,
            intent="technical",
        )
        self.assertTrue(idx._collection_ok)
        self.assertEqual(COLLECTION_NAME, "web_results_786d")

        with patch(
            "kindly_web_search_mcp_server.search.providers.qdrant.AsyncQdrantClient",
            return_value=shared_client,
        ), patch(
            "kindly_web_search_mcp_server.settings.settings.qdrant_space_url",
            "https://fake-url",
        ):
            results = await search_qdrant(
                "FastAPI lifespan",
                num_results=5,
                query_embedding=[0.05] * 786,
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "FastAPI Lifespan Events")
            self.assertEqual(results[0].link, "https://fastapi.tiangolo.com/advanced/events/")

        await shared_client.close()


if __name__ == "__main__":
    unittest.main()
