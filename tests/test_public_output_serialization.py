from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.models import (
    ProviderWarning,
    SearchResultWindow,
    WebSearchResponse,
    WebSearchResult,
)
from kindly_web_search_mcp_server.utils.public_output import (
    serialize_public_web_search_response,
    serialize_public_web_search_result,
)


class TestPublicOutputSerialization(unittest.TestCase):
    def test_public_web_search_result_is_allowlisted(self) -> None:
        result = WebSearchResult(
            title="FastMCP docs",
            link="https://example.com/docs",
            snippet="Docs for the project",
            domain="example.com",
            resource_type="web",
            published_date="2026-05-29",
            mime_hint="text/html",
            source_engines=["searxng"],
            category="docs",
            raw_score=9.5,
            providers=["searxng", "ddg"],
            provider_count=2,
            score=0.99,
            diagnostics=[{"provider": "searxng"}],
        )

        public = serialize_public_web_search_result(result)

        self.assertEqual(
            public,
            {
                "title": "FastMCP docs",
                "link": "https://example.com/docs",
                "snippet": "Docs for the project",
                "domain": "example.com",
                "resource_type": "web",
                "published_date": "2026-05-29",
                "providers": ["searxng", "ddg"],
                "provider_count": 2,
            },
        )

    def test_public_web_search_response_preserves_response_metadata(self) -> None:
        result = WebSearchResult(
            title="FastMCP docs",
            link="https://example.com/docs",
            snippet="Docs for the project",
            providers=["searxng"],
            provider_count=1,
            score=0.42,
        )
        response = WebSearchResponse(
            query="fastmcp docs",
            results=[result],
            total_results=1,
            result_window=SearchResultWindow(
                offset=0,
                returned=1,
                candidate_count=1,
                has_more=False,
            ),
            providers_used=["searxng"],
            warnings=[
                ProviderWarning(provider="ddg", error="timeout", error_type="timeout")
            ],
            diagnostics=[{"provider": "searxng", "score": 0.42}],
        )

        public = serialize_public_web_search_response(response)

        self.assertEqual(public["query"], "fastmcp docs")
        self.assertEqual(public["total_results"], 1)
        self.assertEqual(public["providers_used"], ["searxng"])
        self.assertEqual(public["result_window"]["offset"], 0)
        self.assertEqual(public["warnings"][0]["provider"], "ddg")
        self.assertEqual(public["results"][0]["title"], "FastMCP docs")
        self.assertNotIn("score", public["results"][0])
        self.assertNotIn("diagnostics", public)


if __name__ == "__main__":
    unittest.main()
