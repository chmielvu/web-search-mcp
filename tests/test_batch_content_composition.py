"""Tests for batch_get_content composed via N parallel get_content calls.

Migrated from test_batch_orchestrator.py and adapted to the new architecture
where batch_get_content calls get_content directly.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest


def _make_get_content_result(
    url: str,
    content: str = "",
    *,
    status: str = "success",
    has_more: bool = False,
    next_offset: int | None = None,
) -> dict:
    """Return a dict matching get_content's response shape."""
    total_chars = len(content)
    returned_chars = len(content)
    return {
        "url": url,
        "input_url": url,
        "normalized_url": url,
        "status": status,
        "source_type": "html",
        "fetch_backend": "test",
        "page_content": content,
        "window": {
            "offset": 0,
            "length": 20000,
            "returned_chars": returned_chars,
            "total_chars": total_chars,
            "has_more": has_more,
            "next_offset": next_offset,
            "continuation_notice": (
                f"Truncated. Continue at offset {next_offset}."
                if has_more
                else None
            ),
        },
        "metadata": None,
        "links": None,
        "continuation_notice": (
            f"Truncated. Continue at offset {next_offset}." if has_more else None
        ),
        "content_type": "text/markdown",
        "error": None,
        "content_quality": status,
        "content_word_count": len(content.split()),
        "cached": False,
        "origin_backend": "test",
        "summary": None,
    }


class TestBatchContentComposition(unittest.IsolatedAsyncioTestCase):
    async def test_emits_cursor_when_budget_exhausted(self) -> None:
        """When total_char_budget is too small for all URLs, batch returns
        has_more=True with a cursor containing the unconsumed URLs."""
        from kindly_web_search_mcp_server.tools.content import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        # Each URL returns 3000 chars. Budget is 5000.
        # URL A fits (3000), URL B would push to 6000 > 5000.
        async def _fake_get_content(url: str, **kwargs) -> dict:
            return _make_get_content_result(url, "x" * 3000)

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.get_content",
                new_callable=AsyncMock,
                side_effect=_fake_get_content,
            ),
            patch(
                "kindly_web_search_mcp_server.tools.content.create_batch_summaries",
                new_callable=AsyncMock,
                return_value=[None, None],
            ),
        ):
            output = await batch_get_content(
                urls=["https://a.com", "https://b.com"],
                max_concurrency=1,
                per_item_char_length=10000,
                total_char_budget=5000,
                ctx=mock_ctx,
            )

        self.assertEqual(output["total_requested"], 2)
        self.assertTrue(output["has_more"])
        self.assertIsNotNone(output["cursor"])

        # Decode cursor → should contain the unconsumed URL
        decoded_cursor = json.loads(
            base64.urlsafe_b64decode(output["cursor"].encode())
        )
        self.assertIn("urls", decoded_cursor)
        self.assertTrue(len(decoded_cursor["urls"]) > 0)

    async def test_cursor_continuation_fetches_remaining(self) -> None:
        """Passing the cursor from a prior call should fetch the remaining URLs."""
        from kindly_web_search_mcp_server.tools.content import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        call_count = 0

        async def _fake_get_content(url: str, **kwargs) -> dict:
            nonlocal call_count
            call_count += 1
            return _make_get_content_result(url, "x" * 3000)

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.get_content",
                new_callable=AsyncMock,
                side_effect=_fake_get_content,
            ),
            patch(
                "kindly_web_search_mcp_server.tools.content.create_batch_summaries",
                new_callable=AsyncMock,
                return_value=[None, None, None],
            ),
        ):
            # First call: 2 URLs, budget=5000 → first fits (3000), second exceeds
            out1 = await batch_get_content(
                urls=["https://a.com", "https://b.com"],
                max_concurrency=1,
                per_item_char_length=10000,
                total_char_budget=5000,
                ctx=mock_ctx,
            )
            self.assertTrue(out1["has_more"])

            # Second call with cursor → fetches remaining URL
            out2 = await batch_get_content(
                cursor=out1["cursor"],
                max_concurrency=1,
                per_item_char_length=10000,
                total_char_budget=5000,
                ctx=mock_ctx,
            )
            self.assertFalse(out2["has_more"])

        # First call fetched both URLs (must fetch to know size), but only
        # included URL A in results. Second call fetched URL B via cursor.
        self.assertEqual(call_count, 3)

    async def test_empty_urls_returns_empty_response(self) -> None:
        """Empty URL list returns empty response immediately."""
        from kindly_web_search_mcp_server.tools.content import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        output = await batch_get_content(urls=[], ctx=mock_ctx)
        self.assertEqual(output["total_requested"], 0)
        self.assertEqual(output["total_returned"], 0)
        self.assertFalse(output["has_more"])

    async def test_get_content_error_produces_error_result(self) -> None:
        """If get_content raises, batch captures the error per-URL."""
        from kindly_web_search_mcp_server.tools.content import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        async def _fake_get_content(url: str, **kwargs) -> dict:
            raise ConnectionError("network unreachable")

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.get_content",
                new_callable=AsyncMock,
                side_effect=_fake_get_content,
            ),
            patch(
                "kindly_web_search_mcp_server.tools.content.create_batch_summaries",
                new_callable=AsyncMock,
                return_value=[None],
            ),
        ):
            output = await batch_get_content(
                urls=["https://fail.example.com"],
                ctx=mock_ctx,
            )

        self.assertEqual(output["total_requested"], 1)
        self.assertEqual(output["total_returned"], 1)
        self.assertEqual(output["results"][0]["status"], "error")
        self.assertEqual(output["results"][0]["error"]["code"], "ConnectionError")
        self.assertTrue(output["results"][0]["error"]["retryable"])

    async def test_concurrent_execution_respects_semaphore(self) -> None:
        """Max concurrency is honored — no more than max_concurrency get_content
        calls should be in-flight simultaneously."""
        import asyncio

        from kindly_web_search_mcp_server.tools.content import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()

        async def _fake_get_content(url: str, **kwargs) -> dict:
            nonlocal in_flight, max_observed
            async with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return _make_get_content_result(url, "hello world " * 100)

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.get_content",
                new_callable=AsyncMock,
                side_effect=_fake_get_content,
            ),
            patch(
                "kindly_web_search_mcp_server.tools.content.create_batch_summaries",
                new_callable=AsyncMock,
                return_value=[None] * 6,
            ),
        ):
            await batch_get_content(
                urls=[f"https://{i}.example.com" for i in range(6)],
                max_concurrency=2,
                total_char_budget=100_000,
                ctx=mock_ctx,
            )

        # With max_concurrency=2, at most 2 should be in-flight at once
        self.assertLessEqual(max_observed, 2)


if __name__ == "__main__":
    unittest.main()
