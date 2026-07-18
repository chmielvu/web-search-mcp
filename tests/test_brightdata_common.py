from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GOOGLE_ORGANIC_RESPONSE = {
    "organic": [
        {"title": "Example Result", "link": "https://example.com", "description": "A test result"},
        {"title": "Second Result", "link": "https://example.org", "description": "Another one"},
    ]
}

GOOGLE_NEWS_RESPONSE = {
    "news": [
        {
            "title": "Breaking News",
            "link": "https://news.example.com",
            "description": "Something happened",
            "date": "2 hours ago",
        },
    ]
}


class TestBrightDataURLConstruction(unittest.TestCase):
    def test_google_url_web_defaults(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_google_url

        url = build_google_url("test query")
        self.assertIn("q=test+query", url)
        self.assertIn("gl=us", url)
        self.assertIn("hl=en", url)
        self.assertIn("nfpr=1", url)
        self.assertIn("brd_json=1", url)
        self.assertNotIn("tbm=nws", url)

    def test_google_url_news(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_google_url

        url = build_google_url("openai news", search_type="news")
        self.assertIn("tbm=nws", url)

    def test_google_url_news_freshness_maps_qdr_token(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_google_url

        url = build_google_url("openai news", search_type="news", freshness="week")
        self.assertIn("tbs=qdr:w", url)

    def test_google_url_exact_match_off(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_google_url

        url = build_google_url("code query", exact_match=False)
        self.assertNotIn("nfpr=1", url)

    def test_google_url_custom_country_language(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_google_url

        url = build_google_url("pizza", country="fr", language="fr")
        self.assertIn("gl=fr", url)
        self.assertIn("hl=fr", url)

    def test_bing_url(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import build_bing_url

        url = build_bing_url("test query")
        self.assertIn("q=test+query", url)
        self.assertIn("cc=us", url)
        self.assertIn("setLang=en-US", url)
        self.assertIn("brd_json=1", url)


class TestBrightDataPayload(unittest.TestCase):
    def test_resolve_payload_base_default(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import resolve_payload_base

        payload = resolve_payload_base()
        self.assertEqual(payload["format"], "raw")
        self.assertIn("zone", payload)

    @patch("kindly_web_search_mcp_server.search.providers.brightdata_common.settings")
    def test_resolve_payload_with_extra_json(self, mock_settings):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import resolve_payload_base

        mock_settings.brightdata_payload_extra = '{"method": "GET", "direct": true}'
        payload = resolve_payload_base()
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["direct"], True)

    @patch("kindly_web_search_mcp_server.search.providers.brightdata_common.settings")
    def test_resolve_payload_ignores_bad_json(self, mock_settings):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import resolve_payload_base

        mock_settings.brightdata_payload_extra = "not json"
        payload = resolve_payload_base()
        self.assertNotIn("method", payload)
        self.assertIn("format", payload)


class TestBrightDataErrorDetection(unittest.TestCase):
    def test_detect_upstream_error_returns_none_for_normal_response(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import detect_upstream_error

        self.assertIsNone(detect_upstream_error({"organic": []}))
        self.assertIsNone(detect_upstream_error({"status_code": 200}))

    def test_detect_upstream_error_detects_407(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import detect_upstream_error

        error = detect_upstream_error(
            {
                "status_code": 407,
                "headers": {"x-brd-err-msg": "Invalid authentication"},
            }
        )
        self.assertIsNotNone(error)
        self.assertIn("407", error)
        self.assertIn("Invalid authentication", error)

    def test_detect_upstream_error_with_body(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import detect_upstream_error

        error = detect_upstream_error(
            {"status_code": 502, "headers": {}, "body": "Bad Gateway: upstream server error"}
        )
        self.assertIsNotNone(error)
        self.assertIn("502", error)

    def test_detect_upstream_error_non_dict_returns_none(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import detect_upstream_error

        self.assertIsNone(detect_upstream_error([]))


class TestBrightDataParseResponse(unittest.TestCase):
    def test_parse_organic(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import parse_brightdata_response

        results = parse_brightdata_response(GOOGLE_ORGANIC_RESPONSE, "web", 5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Example Result")

    def test_parse_news(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import parse_brightdata_response

        results = parse_brightdata_response(GOOGLE_NEWS_RESPONSE, "news", 5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].published_date, "2 hours ago")

    def test_parse_raises_on_upstream_error(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import (
            BrightDataError,
            parse_brightdata_response,
        )

        with self.assertRaises(BrightDataError):
            parse_brightdata_response(
                {"status_code": 407, "headers": {"x-brd-err-msg": "auth error"}},
                "web",
                5,
            )

    def test_parse_respects_num_results_limit(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import parse_brightdata_response

        results = parse_brightdata_response(GOOGLE_ORGANIC_RESPONSE, "web", 1)
        self.assertEqual(len(results), 1)

    def test_parse_skips_malformed_items(self):
        from kindly_web_search_mcp_server.search.providers.brightdata_common import parse_brightdata_response

        results = parse_brightdata_response(
            {
                "organic": [
                    {"title": "ok", "link": "https://a.com", "description": "yes"},
                    {"title": "", "link": "https://b.com", "description": "empty title"},
                ]
            },
            "web",
            10,
        )
        self.assertEqual(len(results), 1)
