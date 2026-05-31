from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestBatchOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def test_emits_cursor_when_budget_not_enough(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact
        from kindly_web_search_mcp_server.content.batch_orchestrator import BatchParams, run_batch_fetch

        async def _fake_fetch(url: str) -> ContentArtifact:
            return ContentArtifact(
                input_url=url,
                normalized_url=url,
                fetched_url=url,
                status="success",
                source_type="html",
                fetch_backend="test",
                content_type="text/markdown",
                markdown="x" * 1500,
            )

        with patch(
            "kindly_web_search_mcp_server.content.batch_orchestrator.fetch_content_artifact",
            new=AsyncMock(side_effect=_fake_fetch),
        ):
            output = await run_batch_fetch(
                urls=["https://a.com", "https://b.com"],
                params=BatchParams(max_concurrency=2, per_item_char_length=1000, total_char_budget=1000),
                cursor=None,
            )

        self.assertEqual(output["total_requested"], 2)
        self.assertEqual(output["total_returned"], 1)
        self.assertTrue(output["has_more"])
        self.assertIsNotNone(output["cursor"])
        self.assertIn("continuation_notice", output["results"][0]["window"])
        self.assertIn("Continue at offset", output["results"][0]["window"]["continuation_notice"])

        with patch(
            "kindly_web_search_mcp_server.content.batch_orchestrator.fetch_content_artifact",
            new=AsyncMock(side_effect=_fake_fetch),
        ):
            next_output = await run_batch_fetch(
                urls=["https://a.com", "https://b.com"],
                params=BatchParams(max_concurrency=2, per_item_char_length=1000, total_char_budget=2000),
                cursor=output["cursor"],
            )

        self.assertEqual(next_output["results"][0]["input_url"], "https://a.com")
        self.assertEqual(next_output["results"][0]["window"]["offset"], 1000)

    async def test_per_url_timeout_is_isolated(self) -> None:
        from kindly_web_search_mcp_server.content.batch_orchestrator import BatchParams, run_batch_fetch

        async def _slow_fetch(url: str):
            await asyncio.sleep(0.05)

        with patch(
            "kindly_web_search_mcp_server.content.batch_orchestrator.fetch_content_artifact",
            new=AsyncMock(side_effect=_slow_fetch),
        ):
            output = await run_batch_fetch(
                urls=["https://a.com"],
                params=BatchParams(
                    max_concurrency=1,
                    per_item_char_length=1000,
                    total_char_budget=1000,
                    per_url_timeout_seconds=0.01,
                ),
                cursor=None,
            )

        self.assertEqual(output["results"][0]["status"], "error")
        self.assertEqual(output["results"][0]["error"]["code"], "timeout")


if __name__ == "__main__":
    unittest.main()
