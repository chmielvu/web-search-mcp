from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestComposioStandaloneTools(unittest.TestCase):
    def test_similarlinks_maps_nested_results_without_snippets(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import (
                SIMILARLINKS_SLUG,
                _composio_similarlinks_impl,
            )

            payload = {
                "results": {
                    "results": [
                        {
                            "title": "Middleware - FastMCP",
                            "url": "https://gofastmcp.com/servers/middleware",
                            "score": 0.93,
                        }
                    ]
                }
            }
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _composio_similarlinks_impl(
                    "https://gofastmcp.com/servers/middleware",
                    5,
                    "neural",
                    None,
                    None,
                    None,
                )

            slug, arguments = mock_execute.await_args.args
            self.assertEqual(slug, SIMILARLINKS_SLUG)
            self.assertEqual(arguments["type"], "neural")
            self.assertEqual(arguments["numResults"], 5)
            self.assertEqual(response.total_results, 1)
            self.assertEqual(response.results[0].title, "Middleware - FastMCP")
            self.assertFalse(hasattr(response.results[0], "snippet"))

        anyio.run(run)

    def test_image_search_maps_image_metadata(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import (
                IMAGE_SEARCH_SLUG,
                _composio_image_search_impl,
            )

            payload = {
                "images_results": [
                    {
                        "title": "FastMCP",
                        "source": "FastMCP",
                        "link": "https://gofastmcp.com",
                        "original": "https://cdn.example.com/logo.png",
                        "thumbnail": "https://thumb.example.com/logo.png",
                    }
                ]
            }
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _composio_image_search_impl("FastMCP logo", 4, 0)

            slug, arguments = mock_execute.await_args.args
            self.assertEqual(slug, IMAGE_SEARCH_SLUG)
            self.assertEqual(arguments["query"], "FastMCP logo")
            self.assertEqual(arguments["num"], 4)
            self.assertEqual(arguments["ijn"], 0)
            self.assertEqual(response.total_results, 1)
            self.assertEqual(response.results[0].original_url, "https://cdn.example.com/logo.png")
            self.assertEqual(response.results[0].page_link, "https://gofastmcp.com")

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()


class TestQuickWebSearch(unittest.TestCase):
    def test_quick_web_search_extracts_answer_and_citations(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import (
                WEB_SEARCH_SLUG,
                _quick_web_search_impl,
            )

            payload = {
                "results": {
                    "answer": "Claude Code is Anthropic's official CLI for Claude...",
                    "citations": [
                        {
                            "title": "Claude Code Documentation",
                            "url": "https://claude.ai/code",
                            "snippet": "Claude Code is an interactive agent...",
                        },
                        {
                            "title": "Anthropic Blog",
                            "url": "https://anthropic.com/blog",
                            "snippet": "Announcing Claude Code CLI...",
                        },
                    ]
                }
            }
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _quick_web_search_impl("What is Claude Code?")

            slug, arguments = mock_execute.await_args.args
            self.assertEqual(slug, WEB_SEARCH_SLUG)
            self.assertEqual(arguments["query"], "What is Claude Code?")
            self.assertEqual(response.query, "What is Claude Code?")
            self.assertEqual(response.answer, "Claude Code is Anthropic's official CLI for Claude...")
            self.assertEqual(response.total_citations, 2)
            self.assertEqual(response.citations[0].title, "Claude Code Documentation")
            self.assertEqual(response.citations[0].url, "https://claude.ai/code")

        anyio.run(run)

    def test_quick_web_search_handles_missing_answer(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import _quick_web_search_impl

            payload = {
                "results": {
                    "citations": [
                        {"title": "Source 1", "url": "https://example.com"},
                    ]
                }
            }
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _quick_web_search_impl("test query")

            self.assertIsNone(response.answer)
            self.assertEqual(response.total_citations, 1)

        anyio.run(run)

    def test_quick_web_search_handles_malformed_citations(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import _quick_web_search_impl

            payload = {
                "results": {
                    "answer": "Some answer",
                    "citations": [
                        {"title": "Valid", "url": "https://valid.com"},
                        "not_a_dict",  # skipped - not a dict
                        {"title": None, "url": "https://missing-title.com"},
                    ]
                }
            }
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _quick_web_search_impl("test")

            # Non-dict items are skipped, so we get 2 citations
            self.assertEqual(response.total_citations, 2)
            self.assertEqual(response.citations[0].title, "Valid")
            # Second citation has None title (missing-title.com entry)
            self.assertIsNone(response.citations[1].title)
            self.assertEqual(response.citations[1].url, "https://missing-title.com")

        anyio.run(run)

    def test_quick_web_search_handles_empty_results(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.composio_tools import _quick_web_search_impl

            payload = {"results": {}}
            with patch(
                "kindly_web_search_mcp_server.composio_tools.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                response = await _quick_web_search_impl("test")

            self.assertIsNone(response.answer)
            self.assertEqual(response.total_citations, 0)

        anyio.run(run)
