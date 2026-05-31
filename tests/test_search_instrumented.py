from __future__ import annotations

import unittest
from unittest.mock import patch

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search import ProviderConfig, ProviderMode
from kindly_web_search_mcp_server.search_instrumented import search_single_query


async def _fake_provider(
    query: str,
    *,
    num_results: int,
    http_client: object,
) -> list[WebSearchResult]:
    return [
        WebSearchResult(
            title=f"Result for {query}",
            link="https://example.com/fastmcp",
            snippet=f"limit={num_results} client={bool(http_client)}",
        )
    ]


class TestInstrumentedSearch(unittest.IsolatedAsyncioTestCase):
    async def test_instrumented_search_returns_provider_results(self) -> None:
        config = ProviderConfig(
            name="searxng",
            mode=ProviderMode.ALWAYS,
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        with patch(
            "kindly_web_search_mcp_server.search_instrumented.resolve_providers_for_search",
            return_value=[config],
        ):
            results = await search_single_query("FastMCP docs", num_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].link, "https://example.com/fastmcp")
        self.assertEqual(results[0].providers, ["searxng"])

    async def test_instrumented_search_logs_provider_task_crashes(self) -> None:
        config = ProviderConfig(
            name="searxng",
            mode=ProviderMode.ALWAYS,
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        with (
            patch(
                "kindly_web_search_mcp_server.search_instrumented.resolve_providers_for_search",
                return_value=[config],
            ),
            patch("asyncio.gather", return_value=[RuntimeError("task crashed")]),
        ):
            results = await search_single_query("FastMCP docs", num_results=3)

        self.assertEqual(results, [])
