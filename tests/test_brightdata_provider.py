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
        "kindly_web_search_mcp_server.search.brightdata.search_bing_sidecar",
        new=AsyncMock(return_value=[]),
    )
    @patch(
        "kindly_web_search_mcp_server.search.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    def test_search_returns_google_only_when_bing_disabled(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[
                WebSearchResult(title="Google Hit", link="https://g.com", snippet="from google"),
            ]
        )
        with patch(
            "kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock
        ):
            results = _run_async(search_brightdata("test", num_results=5, use_bing=False))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Google Hit")

    @patch(
        "kindly_web_search_mcp_server.search.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    def test_search_merges_google_and_bing(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[WebSearchResult(title="Google Hit", link="https://g.com", snippet="g")]
        )
        bing_mock = AsyncMock(
            return_value=[WebSearchResult(title="Bing Hit", link="https://b.com", snippet="b")]
        )
        with (
            patch("kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock),
            patch("kindly_web_search_mcp_server.search.brightdata.search_bing_sidecar", bing_mock),
        ):
            results = _run_async(search_brightdata("test", num_results=10))
        self.assertEqual({r.title for r in results}, {"Google Hit", "Bing Hit"})

    @patch(
        "kindly_web_search_mcp_server.search.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    def test_search_news_skips_bing(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[WebSearchResult(title="News Hit", link="https://n.com", snippet="news")]
        )
        with patch(
            "kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock
        ):
            results = _run_async(search_brightdata("test", num_results=5, search_type="news"))
        self.assertEqual(len(results), 1)

    def test_search_returns_empty_for_blank_query(self):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("   ", num_results=5)), [])

    @patch(
        "kindly_web_search_mcp_server.search.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    def test_search_returns_empty_when_no_results(self, *_):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        with (
            patch(
                "kindly_web_search_mcp_server.search.brightdata.run_provider",
                AsyncMock(return_value=[]),
            ),
            patch(
                "kindly_web_search_mcp_server.search.brightdata.search_bing_sidecar",
                AsyncMock(return_value=[]),
            ),
        ):
            results = _run_async(search_brightdata("test", num_results=5))
        self.assertEqual(results, [])

    def test_search_returns_empty_for_zero_results(self):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("test", num_results=0)), [])

    def test_search_bing_propagates_cancellation(self):
        from kindly_web_search_mcp_server.search.brightdata_common import search_bing_sidecar

        class _CancelledClient:
            async def post(self, *args, **kwargs):
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            _run_async(search_bing_sidecar("test", 5, _CancelledClient(), "key", {}, {}, "us", "en"))


class TestRetry429(unittest.TestCase):
    def test_429_is_transient(self):
        from kindly_web_search_mcp_server.retry import is_transient_error

        error = httpx.HTTPStatusError(
            "rate limited",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(429),
        )
        self.assertTrue(is_transient_error(error))

    def test_403_is_not_transient(self):
        from kindly_web_search_mcp_server.retry import is_transient_error

        error = httpx.HTTPStatusError(
            "forbidden",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(403),
        )
        self.assertFalse(is_transient_error(error))
