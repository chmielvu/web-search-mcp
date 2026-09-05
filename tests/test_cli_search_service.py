from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from kindly_web_search_mcp_server.cli.services.search_web import fetch_web_search_payload
from kindly_web_search_mcp_server.models import WebSearchResponse


class TestCliSearchService(IsolatedAsyncioTestCase):
    async def test_fetch_web_search_payload_uses_shared_service_contract(self) -> None:
        http_client = object()
        response = WebSearchResponse(query="FastMCP docs", results=[])
        run = MagicMock()
        run.response = response
        run.plan = None
        run.diagnostics.overflow_ranked = []
        execute = AsyncMock(return_value=(response, run))
        with (
            patch(
                "kindly_web_search_mcp_server.cli.services.search_web.get_http_client",
                AsyncMock(return_value=http_client),
            ),
            patch(
                "kindly_web_search_mcp_server.cli.services.search_web.execute_web_search",
                execute,
            ),
        ):
            await fetch_web_search_payload(
                "FastMCP docs",
                num_results=15,
                rewrite=True,
                research_goal="Find authoritative FastMCP documentation",
            )

        execute.assert_awaited_once()
        request = execute.await_args.args[0]
        self.assertEqual(request.query, "FastMCP docs")
        self.assertEqual(request.research_goal, "Find authoritative FastMCP documentation")
        self.assertEqual(request.num_results, 15)
        self.assertIs(execute.await_args.kwargs["http_client"], http_client)
        self.assertTrue(execute.await_args.kwargs["return_diagnostics"])
