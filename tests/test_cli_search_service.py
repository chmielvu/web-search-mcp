from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.cli.services.search_web import fetch_web_search_payload


class _Response:
    def model_dump(self, exclude_none: bool = True) -> dict[str, object]:
        return {}


class TestCliSearchService(IsolatedAsyncioTestCase):
    async def test_fetch_web_search_payload_passes_diagnostics_none(self) -> None:
        with (
            patch(
                "kindly_web_search_mcp_server.cli.services.search_web.build_search_options",
                return_value="SEARCH_OPTIONS",
            ),
            patch(
                "kindly_web_search_mcp_server.cli.services.search_web.run_search_pipeline",
                new_callable=AsyncMock,
                return_value=_Response(),
            ) as mock_run,
        ):
            await fetch_web_search_payload(
                "FastMCP docs",
                num_results=3,
                rewrite=True,
                providers=["searxng"],
                research_goal=None,
            )

        mock_run.assert_awaited_once()
        _, kwargs = mock_run.await_args
        self.assertIsNone(kwargs["diagnostics"])
