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
            out = await tool_fn("hello", num_results=1, ctx=mock_ctx)

        self.assertIsInstance(out, dict)
        self.assertEqual(out["query"], "hello")
        self.assertIn("results", out)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "T")
        self.assertEqual(out["results"][0]["link"], "https://example.com")
        self.assertEqual(out["results"][0]["snippet"], "S")
        self.assertNotIn("page_content", out["results"][0])

    async def test_get_content_returns_markdown(self) -> None:
        from kindly_web_search_mcp_server.server import get_content

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.resolve_page_content_markdown",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            # Mock page cache to return no cached results (test isolation)
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_resolve.return_value = "# Title\n\nHello"
            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["url"], "https://example.com")
        self.assertIn("page_content", out)
        self.assertIn("Hello", out["page_content"])

    async def test_get_content_handles_none(self) -> None:
        from kindly_web_search_mcp_server.server import get_content

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.resolve_page_content_markdown",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            # Mock page cache to return no cached results (test isolation)
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_resolve.return_value = None
            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com/file.pdf", ctx=mock_ctx)

        self.assertEqual(out["url"], "https://example.com/file.pdf")
        self.assertIn("Could not retrieve content", out["page_content"])

    async def test_get_content_returns_timeout_note_on_timeout(self) -> None:
        from kindly_web_search_mcp_server.server import get_content

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.server.resolve_page_content_markdown",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "kindly_web_search_mcp_server.server.get_page_cache"
        ) as mock_get_page_cache:
            # Mock page cache to return no cached results (test isolation)
            mock_page_cache = MagicMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_resolve.side_effect = asyncio.TimeoutError()
            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertIn("TimeoutError", out["page_content"])
        self.assertIn("Source: https://example.com", out["page_content"])

    async def test_web_search_keeps_results_lightweight_on_cached_search(self) -> None:
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
            out = await tool_fn("hello", num_results=1, ctx=mock_ctx)

        self.assertNotIn("page_content", out["results"][0])


if __name__ == "__main__":
    unittest.main()
