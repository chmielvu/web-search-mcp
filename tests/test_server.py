from __future__ import annotations

import sys
import os
from pathlib import Path
import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResponse, WebSearchResult


class TestWebSearchTool(unittest.IsolatedAsyncioTestCase):
    def test_tool_timeout_budget_can_exceed_55_seconds(self) -> None:
        from kindly_web_search_mcp_server.server import _resolve_tool_total_timeout_seconds

        with patch.dict(
            os.environ,
            {
                "KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS": "120",
                "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "600",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {
                "KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS": "120",
                "KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "100",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 100.0)

        with patch.dict(
            os.environ,
            {"KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS": "abc"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {"KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "abc"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {"KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "90"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 90.0)

    def test_web_search_concurrency_defaults_on_windows(self) -> None:
        from kindly_web_search_mcp_server.server import _resolve_web_search_max_concurrency

        with patch.dict(os.environ, {}, clear=True), patch(
            "kindly_web_search_mcp_server.server.os.name", "nt"
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "3"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "nt"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "abc"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "nt"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "0"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "nt"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "-2"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "nt"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

    def test_web_search_concurrency_limited_by_num_results_on_windows(self) -> None:
        from kindly_web_search_mcp_server.server import _resolve_web_search_max_concurrency

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "10"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "nt"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

    def test_web_search_concurrency_defaults_on_non_windows(self) -> None:
        from kindly_web_search_mcp_server.server import _resolve_web_search_max_concurrency

        with patch.dict(os.environ, {}, clear=True), patch(
            "kindly_web_search_mcp_server.server.os.name", "posix"
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "5"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "posix"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "7"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "posix"):
            self.assertEqual(_resolve_web_search_max_concurrency(5), 5)

        with patch.dict(
            os.environ,
            {"KINDLY_WEB_SEARCH_MAX_CONCURRENCY": "abc"},
            clear=True,
        ), patch("kindly_web_search_mcp_server.server.os.name", "posix"):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

    def test_tool_timeout_defaults_to_120_seconds(self) -> None:
        from kindly_web_search_mcp_server.server import _resolve_tool_total_timeout_seconds

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

    async def test_web_search_returns_results(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        mocked_results = [
            WebSearchResult(title="T", link="https://example.com", snippet="S")
        ]

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.run_web_search", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = WebSearchResponse(query="hello", results=mocked_results)

            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn("hello", "Find information about hello", num_results=1, ctx=mock_ctx)

        self.assertIsInstance(out, dict)
        self.assertEqual(out["query"], "hello")
        self.assertIn("results", out)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "T")
        self.assertEqual(out["results"][0]["link"], "https://example.com")
        self.assertEqual(out["results"][0]["snippet"], "S")
        self.assertNotIn("page_content", out["results"][0])

    async def test_web_search_forwards_search_options_and_window(self) -> None:
        from kindly_web_search_mcp_server.models import SearchResultWindow
        from kindly_web_search_mcp_server.server import web_search
        from kindly_web_search_mcp_server.search.options import SearchOptions

        mocked_results = [
            WebSearchResult(title="T", link="https://example.com", snippet="S")
        ]

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.run_web_search", new_callable=AsyncMock
        ) as mock_search, patch(
            "kindly_web_search_mcp_server.server.get_query_cache"
        ) as mock_get_query_cache, patch(
            "kindly_web_search_mcp_server.server.settings.semantic_cache_enabled", False
        ):
            mock_query_cache = MagicMock()
            mock_query_cache.lookup.return_value = None
            mock_query_cache.store = MagicMock()
            mock_get_query_cache.return_value = mock_query_cache
            mock_search.return_value = WebSearchResponse(
                query="hello",
                results=mocked_results,
                result_window=SearchResultWindow(
                    offset=2,
                    returned=1,
                    candidate_count=5,
                    has_more=True,
                    next_offset=3,
                ),
            )

            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn(
                "hello",
                "Find information about hello",
                num_results=1,
                result_offset=2,
                searxng_categories=["general"],
                searxng_engines=["google"],
                searxng_language="en-US",
                searxng_pageno=2,
                searxng_time_range="week",
                searxng_safesearch=1,
                site_filters=["docs.example.com"],
                domain_filters=["example.com"],
                ctx=mock_ctx,
            )

        forwarded_options = mock_search.await_args.kwargs["search_options"]
        self.assertIsInstance(forwarded_options, SearchOptions)
        self.assertEqual(forwarded_options.result_offset, 2)
        self.assertEqual(forwarded_options.searxng_categories, ("general",))
        self.assertEqual(forwarded_options.searxng_engines, ("google",))
        self.assertEqual(forwarded_options.searxng_language, "en-US")
        self.assertEqual(forwarded_options.searxng_pageno, 2)
        self.assertEqual(forwarded_options.searxng_time_range, "week")
        self.assertEqual(forwarded_options.searxng_safesearch, 1)
        self.assertEqual(forwarded_options.site_filters, ("docs.example.com",))
        self.assertEqual(forwarded_options.domain_filters, ("example.com",))
        self.assertIn("result_window", out)
        self.assertEqual(out["result_window"]["next_offset"], 3)

    async def test_get_content_returns_markdown(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.fetch_content_artifact",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com",
                normalized_url="https://example.com",
                fetched_url="https://example.com/",
                status="success",
                source_type="html",
                fetch_backend="test",
                content_type="text/markdown",
                markdown="# Title\n\nHello",
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["input_url"], "https://example.com")
        self.assertEqual(out["normalized_url"], "https://example.com")
        self.assertEqual(out["fetched_url"], "https://example.com/")
        self.assertEqual(out["source_type"], "html")
        self.assertEqual(out["fetch_backend"], "test")
        self.assertIn("page_content", out)
        self.assertIn("Hello", out["page_content"])
        self.assertEqual(out["window"]["total_chars"], len("# Title\n\nHello"))

    async def test_get_content_includes_metadata_and_links_when_requested(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.fetch_content_artifact",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com",
                normalized_url="https://example.com",
                fetched_url="https://example.com/",
                status="success",
                source_type="html",
                fetch_backend="test",
                content_type="text/markdown",
                markdown="First paragraph.\n\nSecond paragraph.",
                metadata={"title": "Example"},
                links=[
                    {
                        "url": "https://example.com/next",
                        "text": "Next",
                        "domain": "example.com",
                        "internal": True,
                    }
                ],
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn(
                "https://example.com",
                char_length=20,
                include_links=True,
                max_links=5,
                ctx=mock_ctx,
            )

        self.assertEqual(out["metadata"]["title"], "Example")
        self.assertEqual(out["links"][0]["url"], "https://example.com/next")
        self.assertIn("continuation_notice", out)
        self.assertIn("Continue at offset", out["continuation_notice"])

    async def test_get_content_returns_structured_error_artifact(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact, ContentError
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.fetch_content_artifact",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com/file.pdf",
                normalized_url="https://example.com/file.pdf",
                fetched_url=None,
                status="unsupported",
                source_type="pdf",
                fetch_backend="pdf_extract",
                content_type="application/pdf",
                markdown="",
                error=ContentError(code="pdf_extract_failed", message="bad pdf"),
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com/file.pdf", ctx=mock_ctx)

        self.assertEqual(out["input_url"], "https://example.com/file.pdf")
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["error"]["code"], "pdf_extract_failed")
        self.assertEqual(out["window"]["total_chars"], 0)

    async def test_get_content_returns_structured_timeout_error(self) -> None:
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.fetch_content_artifact",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache, patch(
            "kindly_web_search_mcp_server.server._resolve_tool_total_timeout_seconds",
            return_value=0.01,
        ):
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.side_effect = asyncio.TimeoutError()
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["status"], "error")
        self.assertEqual(out["fetched_url"], None)
        self.assertEqual(out["error"]["code"], "timeout")

    async def test_web_search_keeps_results_lightweight_on_cached_search(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        mocked_results = [
            WebSearchResult(
                title="T",
                link="https://example.com",
                snippet="S",
                domain="example.com",
                resource_type="web",
                mime_hint="text/html",
                published_date="2026-05-29",
                source_engines=["searxng"],
                category="docs",
                raw_score=3.14,
                providers=["searxng", "ddg"],
                provider_count=2,
                score=0.92,
                diagnostics=[{"provider": "searxng"}],
            )
        ]

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.run_web_search", new_callable=AsyncMock
        ) as mock_search, patch(
            "kindly_web_search_mcp_server.server.get_query_cache"
        ) as mock_get_query_cache, patch(
            "kindly_web_search_mcp_server.server.settings.semantic_cache_enabled",
            False,
        ):
            mock_query_cache = MagicMock()
            mock_query_cache.lookup.return_value = None
            mock_query_cache.store = MagicMock()
            mock_get_query_cache.return_value = mock_query_cache
            mock_search.return_value = WebSearchResponse(query="hello", results=mocked_results)
            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn("hello", "Find information about hello", num_results=1, ctx=mock_ctx)

        self.assertNotIn("page_content", out["results"][0])
        self.assertEqual(out["results"][0]["domain"], "example.com")
        self.assertEqual(out["results"][0]["resource_type"], "web")
        self.assertEqual(out["results"][0]["published_date"], "2026-05-29")
        self.assertEqual(out["results"][0]["providers"], ["searxng", "ddg"])
        self.assertEqual(out["results"][0]["provider_count"], 2)
        self.assertNotIn("mime_hint", out["results"][0])
        self.assertNotIn("source_engines", out["results"][0])
        self.assertNotIn("category", out["results"][0])
        self.assertNotIn("raw_score", out["results"][0])
        self.assertNotIn("score", out["results"][0])
        self.assertNotIn("diagnostics", out["results"][0])

    async def test_discover_links_returns_links(self) -> None:
        from kindly_web_search_mcp_server.server import discover_links

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.discover_page_links",
            new_callable=AsyncMock,
        ) as mock_discover:
            mock_discover.return_value = {
                "input_url": "https://example.com",
                "normalized_url": "https://example.com",
                "fetched_url": "https://example.com/",
                "source_type": "html",
                "links": [
                    {
                        "url": "https://example.com/next",
                        "text": "Next",
                        "domain": "example.com",
                        "internal": True,
                    }
                ],
                "returned_links": 1,
                "has_more": False,
                "metadata": {"title": "Example"},
            }

            tool_fn = discover_links.fn if hasattr(discover_links, "fn") else discover_links
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["source_type"], "html")
        self.assertEqual(out["links"][0]["url"], "https://example.com/next")
        self.assertEqual(out["metadata"]["title"], "Example")


if __name__ == "__main__":
    unittest.main()
