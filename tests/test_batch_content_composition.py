"""Tests for unified fetch single/bulk composition."""

from __future__ import annotations

import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.tools.content import fetch


def _artifact(url: str, content: str) -> dict:
    return {
        "input_url": url,
        "normalized_url": url,
        "fetched_url": url,
        "status": "success",
        "source_type": "html",
        "fetch_backend": "test",
        "origin_backend": "test",
        "cached": False,
        "content_type": "text/markdown",
        "markdown": content,
        "metadata": None,
        "links": None,
        "error": None,
        "entities": None,
        "llms_txt": None,
        "diagnostics": None,
    }


class TestUnifiedFetch(unittest.IsolatedAsyncioTestCase):
    def _ctx(self) -> AsyncMock:
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.report_progress = AsyncMock()
        return ctx

    async def test_bulk_defers_tail_when_hidden_budget_is_exhausted(self) -> None:
        urls = [f"https://{letter}.example.com" for letter in ("a", "b", "c")]
        content = "x" * 60_000
        with (
            patch.object(settings, "web_fetch_item_max_chars", 60_000),
            patch.object(settings, "web_fetch_total_char_budget", 120_000),
            patch.object(settings, "web_fetch_wave_size", 10),
            patch.object(settings, "web_fetch_workers", 4),
            patch.object(settings, "web_fetch_wave_delay_seconds", 0.0),
            patch(
                "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
                new_callable=AsyncMock,
                side_effect=lambda url, **_: _artifact(url, content),
            ),
        ):
            first = await fetch(urls=urls, ctx=self._ctx())
            first = first.model_dump(exclude_none=True)

        self.assertEqual(first["mode"], "bulk")
        self.assertEqual(first["total_returned"], 2)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["cursor"])
        pending = json.loads(base64.urlsafe_b64decode(first["cursor"].encode()).decode())
        self.assertEqual(pending["version"], 1)
        self.assertEqual(pending["mode"], "bulk")
        self.assertEqual(pending["urls"], [urls[2]])

        with (
            patch.object(settings, "web_fetch_item_max_chars", 60_000),
            patch.object(settings, "web_fetch_total_char_budget", 120_000),
            patch.object(settings, "web_fetch_wave_size", 10),
            patch.object(settings, "web_fetch_workers", 4),
            patch(
                "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
                new_callable=AsyncMock,
                side_effect=lambda url, **_: _artifact(url, content),
            ),
        ):
            second = await fetch(cursor=first["cursor"], ctx=self._ctx())
            second = second.model_dump(exclude_none=True)

        self.assertEqual(second["mode"], "bulk")
        self.assertEqual(second["total_returned"], 1)
        self.assertFalse(second["has_more"])

    async def test_bulk_runs_fixed_waves(self) -> None:
        urls = [f"https://{index}.example.com" for index in range(11)]
        calls: list[str] = []

        async def fake_fetch(url: str, **_: object) -> dict:
            calls.append(url)
            return _artifact(url, "short content")

        with (
            patch.object(settings, "web_fetch_wave_size", 10),
            patch.object(settings, "web_fetch_workers", 4),
            patch.object(settings, "web_fetch_wave_delay_seconds", 0.0),
            patch(
                "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
                new_callable=AsyncMock,
                side_effect=fake_fetch,
            ),
        ):
            output = await fetch(urls=urls, ctx=self._ctx())
            output = output.model_dump(exclude_none=True)

        self.assertEqual(output["total_requested"], 11)
        self.assertEqual(output["total_returned"], 11)
        self.assertEqual(output["waves_completed"], 2)
        self.assertEqual(calls, urls)

    async def test_internal_workers_are_bounded(self) -> None:
        urls = [f"https://{index}.example.com" for index in range(10)]
        in_flight = 0
        maximum = 0
        lock = asyncio.Lock()

        async def fake_fetch(url: str, **_: object) -> dict:
            nonlocal in_flight, maximum
            async with lock:
                in_flight += 1
                maximum = max(maximum, in_flight)
            await asyncio.sleep(0.01)
            async with lock:
                in_flight -= 1
            return _artifact(url, "short content")

        with (
            patch.object(settings, "web_fetch_workers", 4),
            patch.object(settings, "web_fetch_wave_size", 10),
            patch.object(settings, "web_fetch_wave_delay_seconds", 0.0),
            patch(
                "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
                new_callable=AsyncMock,
                side_effect=fake_fetch,
            ),
        ):
            await fetch(urls=urls, ctx=self._ctx())

        self.assertLessEqual(maximum, 4)

    async def test_mode_validation_and_deduplication(self) -> None:
        with self.assertRaises(ValueError):
            await fetch(ctx=self._ctx())

        with patch(
            "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
            new_callable=AsyncMock,
            side_effect=lambda url, **_: _artifact(url, "content"),
        ):
            single = await fetch(
                url="https://example.com",
                urls=["https://example.com"],
                ctx=self._ctx(),
            )
        single = single.model_dump(exclude_none=True)
        self.assertEqual(single["mode"], "single")
        self.assertEqual(single["total_requested"], 1)


if __name__ == "__main__":
    unittest.main()
