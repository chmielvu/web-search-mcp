from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResponse, WebSearchResult


class TestPageContentResolver(unittest.IsolatedAsyncioTestCase):
    async def test_web_search_keeps_results_lightweight_for_stackexchange(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        search_results = [
            WebSearchResult(
                title="SO",
                link="https://stackoverflow.com/questions/11227809/example",
                snippet="snippet",
            )
        ]

        mock_ctx = MagicMock()
        mock_ctx.report_progress = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch("fastmcp.server.context.request_id", new_callable=MagicMock),
            patch("fastmcp.server.context._session", new_callable=MagicMock),
        ):
            with (
                patch(
                    "kindly_web_search_mcp_server.server.run_web_search",
                    new_callable=AsyncMock,
                ) as mock_search,
            ):
                mock_search.return_value = WebSearchResponse(
                    query="q", results=search_results
                )

                # Pass ctx explicitly to bypass CurrentContext() injection
                out = await web_search(
                    "q", research_goal="testing", num_results=1, ctx=mock_ctx
                )

        self.assertNotIn("page_content", out["results"][0])

    async def test_web_search_keeps_results_lightweight_for_pdf(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        results = [
            WebSearchResult(
                title="PDF",
                link="https://example.com/file.pdf",
                snippet="snippet",
            )
        ]

        mock_ctx = MagicMock()
        mock_ctx.report_progress = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch("fastmcp.server.context.request_id", new_callable=MagicMock),
            patch("fastmcp.server.context._session", new_callable=MagicMock),
        ):
            with (
                patch(
                    "kindly_web_search_mcp_server.server.run_web_search",
                    new_callable=AsyncMock,
                ) as mock_search,
            ):
                mock_search.return_value = WebSearchResponse(query="q", results=results)

                out = await web_search(
                    "q", research_goal="testing", num_results=1, ctx=mock_ctx
                )

        self.assertNotIn("page_content", out["results"][0])


if __name__ == "__main__":
    unittest.main()
