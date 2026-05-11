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
