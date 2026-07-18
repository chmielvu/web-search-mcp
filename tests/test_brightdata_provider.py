from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _run_async(awaitable):
    return asyncio.run(awaitable)


class TestBrightDataSearchIntegration(unittest.TestCase):
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_brightdata_default_uses_google(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[
                WebSearchResult(title="Google Hit", link="https://g.com", snippet="from google"),
            ]
        )
        bing_mock = AsyncMock(
            return_value=[WebSearchResult(title="Bing Hit", link="https://b.com", snippet="b")]
        )
        with (
            patch("kindly_web_search_mcp_server.search.providers.brightdata.run_provider", run_provider_mock),
            patch("kindly_web_search_mcp_server.search.providers.brightdata._search_bing", bing_mock),
        ):
            results = _run_async(search_brightdata("test", num_results=5))
        bing_mock.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Google Hit")

    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_news_skips_bing(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[WebSearchResult(title="News Hit", link="https://n.com", snippet="news")]
        )
        with patch(
            "kindly_web_search_mcp_server.search.providers.brightdata.run_provider", run_provider_mock
        ):
            results = _run_async(search_brightdata("test", num_results=5, search_type="news"))
        self.assertEqual(len(results), 1)

    def test_search_returns_empty_for_blank_query(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("   ", num_results=5)), [])

    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_returns_empty_when_no_results(self, *_):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.run_provider",
                AsyncMock(return_value=[]),
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata._search_bing",
                AsyncMock(return_value=[]),
            ),
        ):
            results = _run_async(search_brightdata("test", num_results=5))
        self.assertEqual(results, [])

    def test_search_returns_empty_for_zero_results(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("test", num_results=0)), [])

    def test_search_bing_propagates_cancellation(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import _search_bing

        class _CancelledClient:
            async def post(self, *args, **kwargs):
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            _run_async(
                _search_bing("test", 5, _CancelledClient(), "key", {}, {}, "us", "en")
            )
