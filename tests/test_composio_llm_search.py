from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestComposioLLMSearch(unittest.TestCase):
    def test_search_composio_llm_search_maps_results(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.search.composio_llm_search import (
                COMPOSIO_LLM_SEARCH_SLUG,
                search_composio_llm_search,
            )

            payload = {
                "results": [
                    {
                        "title": "Middleware - FastMCP",
                        "url": "https://gofastmcp.com/servers/middleware",
                        "content": "Add cross-cutting functionality.",
                        "score": 0.76,
                    }
                ],
                "answer": "Ignored answer",
            }

            with patch(
                "kindly_web_search_mcp_server.search.composio_llm_search.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = payload
                timeout_obj = SimpleNamespace(connect=7.0, read=9.0, write=8.0, pool=6.0)
                fake_http_client = SimpleNamespace(timeout=timeout_obj)
                results = await search_composio_llm_search(
                    "fastmcp",
                    num_results=3,
                    http_client=fake_http_client,
                )

            mock_execute.assert_awaited_once()
            slug, arguments = mock_execute.await_args.args
            kwargs = mock_execute.await_args.kwargs
            self.assertEqual(slug, COMPOSIO_LLM_SEARCH_SLUG)
            self.assertEqual(arguments["query"], "fastmcp")
            self.assertFalse(arguments["include_answer"])
            self.assertFalse(arguments["include_images"])
            self.assertFalse(arguments["include_raw_content"])
            self.assertEqual(kwargs["timeout_seconds"], 9.0)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Middleware - FastMCP")
            self.assertEqual(results[0].link, "https://gofastmcp.com/servers/middleware")
            self.assertEqual(results[0].snippet, "Add cross-cutting functionality.")
            self.assertEqual(results[0].providers, ["composio_llm_search"])

        anyio.run(run)

    def test_search_composio_llm_search_rejects_malformed_results(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.search.composio_llm_search import (
                ComposioLLMSearchError,
                search_composio_llm_search,
            )

            with patch(
                "kindly_web_search_mcp_server.search.composio_llm_search.execute_composio_tool",
                new_callable=AsyncMock,
            ) as mock_execute:
                mock_execute.return_value = {"results": {"not": "a list"}}
                with self.assertRaises(ComposioLLMSearchError):
                    await search_composio_llm_search("fastmcp", num_results=3)

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()
