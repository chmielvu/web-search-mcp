from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _run_async(awaitable):
    import asyncio
    return asyncio.run(awaitable)


GOOGLE_ORGANIC_RESPONSE = {
    "organic": [
        {"title": "Example Result", "link": "https://example.com", "description": "A test result"},
        {"title": "Second Result", "link": "https://example.org", "description": "Another one"},
    ]
}

GOOGLE_NEWS_RESPONSE = {
    "news": [
        {"title": "Breaking News", "link": "https://news.example.com", "description": "Something happened", "date": "2 hours ago"},
    ]
}

BING_RESPONSE = {
    "organic": [
        {"title": "Bing Result", "link": "https://bing.example.com", "description": "From Bing"},
    ]
}


class TestBrightDataURLConstruction(unittest.TestCase):
    def test_google_url_web_defaults(self):
        from kindly_web_search_mcp_server.search.brightdata import _build_google_url

        url = _build_google_url("test query")
        self.assertIn("q=test+query", url)
        self.assertIn("gl=us", url)
        self.assertIn("hl=en", url)
        self.assertIn("nfpr=1", url)
        self.assertIn("brd_json=1", url)
        self.assertNotIn("tbm=nws", url)

    def test_google_url_news(self):
        from kindly_web_search_mcp_server.search.brightdata import _build_google_url

        url = _build_google_url("openai news", search_type="news")
        self.assertIn("tbm=nws", url)

    def test_google_url_exact_match_off(self):
        from kindly_web_search_mcp_server.search.brightdata import _build_google_url

        url = _build_google_url("code query", exact_match=False)
        self.assertNotIn("nfpr=1", url)

    def test_google_url_custom_country_language(self):
        from kindly_web_search_mcp_server.search.brightdata import _build_google_url

        url = _build_google_url("pizza", country="fr", language="fr")
        self.assertIn("gl=fr", url)
        self.assertIn("hl=fr", url)

    def test_bing_url(self):
        from kindly_web_search_mcp_server.search.brightdata import _build_bing_url

        url = _build_bing_url("test query")
        self.assertIn("q=test+query", url)
        self.assertIn("cc=us", url)
        self.assertIn("setLang=en-US", url)


class TestBrightDataPayload(unittest.TestCase):
    def test_resolve_payload_base_default(self):
        from kindly_web_search_mcp_server.search.brightdata import _resolve_payload_base

        payload = _resolve_payload_base()
        self.assertEqual(payload["format"], "raw")
        self.assertEqual(payload["data_format"], "parsed_light")
        self.assertIn("zone", payload)

    @patch("kindly_web_search_mcp_server.search.brightdata.settings")
    def test_resolve_payload_with_extra_json(self, mock_settings):
        from kindly_web_search_mcp_server.search.brightdata import _resolve_payload_base

        mock_settings.brightdata_payload_extra = '{"method": "GET", "direct": true}'
        payload = _resolve_payload_base()
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["direct"], True)

    @patch("kindly_web_search_mcp_server.search.brightdata.settings")
    def test_resolve_payload_ignores_bad_json(self, mock_settings):
        from kindly_web_search_mcp_server.search.brightdata import _resolve_payload_base

        mock_settings.brightdata_payload_extra = 'not json'
        payload = _resolve_payload_base()
        self.assertNotIn("method", payload)
        self.assertIn("format", payload)


class TestBrightDataErrorDetection(unittest.TestCase):
    def test_detect_upstream_error_returns_none_for_normal_response(self):
        from kindly_web_search_mcp_server.search.brightdata import _detect_upstream_error

        self.assertIsNone(_detect_upstream_error({"organic": []}))
        self.assertIsNone(_detect_upstream_error({"status_code": 200}))

    def test_detect_upstream_error_detects_407(self):
        from kindly_web_search_mcp_server.search.brightdata import _detect_upstream_error

        error = _detect_upstream_error({
            "status_code": 407,
            "headers": {"x-brd-err-msg": "Invalid authentication"},
        })
        self.assertIsNotNone(error)
        self.assertIn("407", error)
        self.assertIn("Invalid authentication", error)

    def test_detect_upstream_error_with_body(self):
        from kindly_web_search_mcp_server.search.brightdata import _detect_upstream_error

        error = _detect_upstream_error({
            "status_code": 502,
            "headers": {},
            "body": "Bad Gateway: upstream server error",
        })
        self.assertIsNotNone(error)
        self.assertIn("502", error)
        self.assertIn("Bad Gateway", error)

    def test_detect_upstream_error_non_dict_returns_none(self):
        from kindly_web_search_mcp_server.search.brightdata import _detect_upstream_error

        self.assertIsNone(_detect_upstream_error([]))
        self.assertIsNone(_detect_upstream_error("string"))


class TestBrightDataParseResponse(unittest.TestCase):
    def test_parse_organic(self):
        from kindly_web_search_mcp_server.search.brightdata import _parse_response

        results = _parse_response(GOOGLE_ORGANIC_RESPONSE, "web", 5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example Result")
        self.assertEqual(results[0].link, "https://example.com")
        self.assertEqual(results[0].snippet, "A test result")

    def test_parse_news(self):
        from kindly_web_search_mcp_server.search.brightdata import _parse_response

        results = _parse_response(GOOGLE_NEWS_RESPONSE, "news", 5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Breaking News")
        self.assertEqual(results[0].published_date, "2 hours ago")

    def test_parse_respects_num_results_limit(self):
        from kindly_web_search_mcp_server.search.brightdata import _parse_response

        results = _parse_response(GOOGLE_ORGANIC_RESPONSE, "web", 1)
        self.assertEqual(len(results), 1)

    def test_parse_raises_on_upstream_error(self):
        from kindly_web_search_mcp_server.search.brightdata import _parse_response, BrightDataError

        with self.assertRaises(BrightDataError):
            _parse_response({
                "status_code": 407,
                "headers": {"x-brd-err-msg": "auth error"},
            }, "web", 5)

    def test_parse_skips_malformed_items(self):
        from kindly_web_search_mcp_server.search.brightdata import _parse_response

        results = _parse_response({
            "organic": [
                {"title": "ok", "link": "https://a.com", "description": "yes"},
                {"title": "", "link": "https://b.com", "description": "empty title"},
                {"title": 123, "link": "https://c.com", "description": "not string"},
                {"title": "no-link", "link": "", "description": "nope"},
                {"title": None, "link": None, "description": None},
            ]
        }, "web", 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "ok")


class TestBrightDataSearchIntegration(unittest.TestCase):
    @patch("kindly_web_search_mcp_server.search.brightdata.run_provider")
    @patch("kindly_web_search_mcp_server.search.brightdata._search_bing", new=AsyncMock(return_value=[]))
    @patch("kindly_web_search_mcp_server.search.brightdata._get_brightdata_api_key", return_value="test-key")
    @patch("kindly_web_search_mcp_server.search.brightdata._resolve_payload_base", return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"})
    def test_search_returns_google_only_when_bing_disabled(self, *_):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata
        from kindly_web_search_mcp_server.models import WebSearchResult

        run_provider_mock = AsyncMock(return_value=[
            WebSearchResult(title="Google Hit", link="https://g.com", snippet="from google"),
        ])

        with patch("kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock):
            results = _run_async(search_brightdata("test", num_results=5, use_bing=False))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Google Hit")

    @patch("kindly_web_search_mcp_server.search.brightdata._get_brightdata_api_key", return_value="test-key")
    @patch("kindly_web_search_mcp_server.search.brightdata._resolve_payload_base", return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"})
    def test_search_merges_google_and_bing(self, *_):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata
        from kindly_web_search_mcp_server.models import WebSearchResult

        run_provider_mock = AsyncMock(return_value=[
            WebSearchResult(title="Google Hit", link="https://g.com", snippet="g"),
        ])
        bing_mock = AsyncMock(return_value=[
            WebSearchResult(title="Bing Hit", link="https://b.com", snippet="b"),
        ])

        with (
            patch("kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock),
            patch("kindly_web_search_mcp_server.search.brightdata._search_bing", bing_mock),
        ):
            results = _run_async(search_brightdata("test", num_results=10))

        self.assertEqual(len(results), 2)
        titles = {r.title for r in results}
        self.assertEqual(titles, {"Google Hit", "Bing Hit"})

    @patch("kindly_web_search_mcp_server.search.brightdata._get_brightdata_api_key", return_value="test-key")
    @patch("kindly_web_search_mcp_server.search.brightdata._resolve_payload_base", return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"})
    def test_search_raises_when_no_results(self, *_):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata, BrightDataError

        run_provider_mock = AsyncMock(return_value=[])
        bing_mock = AsyncMock(return_value=[])

        with (
            patch("kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock),
            patch("kindly_web_search_mcp_server.search.brightdata._search_bing", bing_mock),
        ):
            with self.assertRaises(BrightDataError):
                _run_async(search_brightdata("test", num_results=5))

    @patch("kindly_web_search_mcp_server.search.brightdata._get_brightdata_api_key", return_value="test-key")
    @patch("kindly_web_search_mcp_server.search.brightdata._resolve_payload_base", return_value={"zone": "test", "format": "raw", "data_format": "parsed_light"})
    def test_search_news_skips_bing(self, *_):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata
        from kindly_web_search_mcp_server.models import WebSearchResult

        run_provider_mock = AsyncMock(return_value=[
            WebSearchResult(title="News Hit", link="https://n.com", snippet="news"),
        ])

        with patch("kindly_web_search_mcp_server.search.brightdata.run_provider", run_provider_mock):
            results = _run_async(search_brightdata("test", num_results=5, search_type="news"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "News Hit")

    def test_search_returns_empty_for_blank_query(self):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        results = _run_async(search_brightdata("   ", num_results=5))
        self.assertEqual(results, [])

    def test_search_returns_empty_for_zero_results(self):
        from kindly_web_search_mcp_server.search.brightdata import search_brightdata

        results = _run_async(search_brightdata("test", num_results=0))
        self.assertEqual(results, [])


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


if __name__ == "__main__":
    unittest.main()
