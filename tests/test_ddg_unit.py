"""Tests for DuckDuckGo search provider."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.ddg import search_ddg, _search_ddg_sync
from kindly_web_search_mcp_server.models import WebSearchResult


class TestDDGSearch(unittest.TestCase):
    """Test DDG search provider."""

    def test_search_ddg_empty_query(self) -> None:
        async def run() -> None:
            results = await search_ddg("", num_results=5)
            self.assertEqual(results, [])

        asyncio.run(run())

    def test_search_ddg_zero_results(self) -> None:
        async def run() -> None:
            results = await search_ddg("test", num_results=0)
            self.assertEqual(results, [])

        asyncio.run(run())

    def test_search_ddg_negative_results(self) -> None:
        async def run() -> None:
            results = await search_ddg("test", num_results=-1)
            self.assertEqual(results, [])

        asyncio.run(run())

    def test_search_ddg_whitespace_query(self) -> None:
        async def run() -> None:
            results = await search_ddg("   ", num_results=5)
            self.assertEqual(results, [])

        asyncio.run(run())

    def test_search_ddg_returns_results(self) -> None:
        async def run() -> None:
            mock_results = [
                WebSearchResult(
                    title="Test Result 1",
                    link="https://example.com/1",
                    snippet="Test snippet 1",
                    providers=["ddg"],
                ),
                WebSearchResult(
                    title="Test Result 2",
                    link="https://example.com/2",
                    snippet="Test snippet 2",
                    providers=["ddg"],
                ),
            ]

            with patch(
                "kindly_web_search_mcp_server.search.ddg._search_ddg_sync",
                return_value=mock_results,
            ):
                results = await search_ddg("test query", num_results=5)

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].title, "Test Result 1")
            self.assertEqual(results[0].link, "https://example.com/1")
            self.assertEqual(results[0].providers, ["ddg"])

        asyncio.run(run())

    def test_search_ddg_handles_exception(self) -> None:
        async def run() -> None:
            with patch(
                "kindly_web_search_mcp_server.search.ddg._search_ddg_sync",
                side_effect=Exception("DDG error"),
            ):
                results = await search_ddg("test query", num_results=5)

            # Should return empty list on error
            self.assertEqual(results, [])

        asyncio.run(run())


class TestDDGSyncSearch(unittest.TestCase):
    """Test synchronous DDG search helper."""

    def test_search_ddg_sync_mocked(self) -> None:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"title": "Test", "href": "https://example.com", "body": "Snippet"}
        ]
        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Test")
        self.assertEqual(results[0].link, "https://example.com")
        self.assertEqual(results[0].snippet, "Snippet")
        self.assertEqual(results[0].providers, ["ddg"])

    def test_search_ddg_sync_missing_title(self) -> None:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"href": "https://example.com", "body": "Snippet"}  # Missing title
        ]
        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=5)

        # Should skip results without title
        self.assertEqual(len(results), 0)

    def test_search_ddg_sync_missing_link(self) -> None:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"title": "Test", "body": "Snippet"}  # Missing link
        ]
        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=5)

        # Should skip results without link
        self.assertEqual(len(results), 0)

    def test_search_ddg_sync_missing_snippet(self) -> None:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"title": "Test", "href": "https://example.com"}  # Missing snippet
        ]
        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=5)

        # Should include result with empty snippet
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].snippet, "")

    def test_search_ddg_sync_respects_limit(self) -> None:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
        mock_ddgs_instance.text.return_value = [
            {"title": f"Result {i}", "href": f"https://example.com/{i}", "body": f"Snippet {i}"}
            for i in range(10)
        ]
        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=3)

        # Should limit to requested count
        self.assertEqual(len(results), 3)

        with patch(
            "ddgs.DDGS",
            mock_ddgs_class,
        ):
            results = _search_ddg_sync("test query", num_results=3)

        # Should limit to requested count
        self.assertEqual(len(results), 3)

    def test_search_ddg_sync_import_error(self) -> None:
        with patch(
            "ddgs.DDGS",
            side_effect=ImportError("No module"),
        ):
            results = _search_ddg_sync("test query", num_results=5)

        # Should return empty list on ImportError
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()