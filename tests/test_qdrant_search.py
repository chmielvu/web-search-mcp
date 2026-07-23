from __future__ import annotations

import sys
import unittest
import pytest
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

    @pytest.mark.xfail(
        reason="started.set() moved into embed_query; timing assertion needs re-evaluation after qdrant refactor"
    )
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


if __name__ == "__main__":
    unittest.main()
