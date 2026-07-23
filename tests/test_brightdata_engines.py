from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _run_async(awaitable):
    return asyncio.run(awaitable)


class TestBrightDataEngineUrls(unittest.TestCase):
    def test_bing_url_includes_brd_json(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_bing_url

        url = build_bing_url("test query")
        self.assertIn("brd_json=1", url)

    def test_yandex_url_includes_region_and_language(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_yandex_url

        url = build_yandex_url("test query", region="84", language="en")
        self.assertIn("text=test+query", url)
        self.assertIn("lr=84", url)
        self.assertIn("lang=en", url)

    def test_invalid_provider_name_raises(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        with self.assertRaises(ValueError):
            _run_async(search_brightdata("q", num_results=5, provider_name="brightdata_google"))


class TestBrightDataAliasRouting(unittest.TestCase):
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata._search_bing",
        new_callable=AsyncMock,
    )
    def test_bing_alias_uses_sidecar_not_google(self, sidecar_mock, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        sidecar_mock.return_value = [
            WebSearchResult(title="Bing Primary", link="https://b.com", snippet="b"),
        ]
        with patch(
            "kindly_web_search_mcp_server.search.providers.brightdata.run_provider",
            new_callable=AsyncMock,
        ) as run_mock:
            results = _run_async(
                search_brightdata("test", num_results=5, provider_name="brightdata_bing")
            )
            run_mock.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Bing Primary")

    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"},
    )
    def test_yandex_alias_calls_run_provider_with_name(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        run_mock = AsyncMock(
            return_value=[WebSearchResult(title="Yandex", link="https://y.com", snippet="y")]
        )
        with patch(
            "kindly_web_search_mcp_server.search.providers.brightdata.run_provider", run_mock
        ):
            results = _run_async(
                search_brightdata("test", num_results=5, provider_name="brightdata_yandex")
            )
            self.assertEqual(run_mock.call_args.kwargs["provider_name"], "brightdata_yandex")
        self.assertEqual(len(results), 1)
