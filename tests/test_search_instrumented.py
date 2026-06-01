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
            source_engines=["searxng-engine-a"],
            raw_score=0.91,
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

    async def test_instrumented_search_persists_raw_provider_results(self) -> None:
        config = ProviderConfig(
            name="searxng",
            mode=ProviderMode.ALWAYS,
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        captured_calls: list[tuple[str, dict[str, object]]] = []

        def _capture(event_name: str, payload: dict[str, object], *, db_path=None):
            captured_calls.append((event_name, payload))

        with (
            patch(
                "kindly_web_search_mcp_server.search_instrumented.resolve_providers_for_search",
                return_value=[config],
            ),
            patch("kindly_web_search_mcp_server.analytics.duckdb_store.append_event", side_effect=_capture),
        ):
            await search_single_query("FastMCP docs", num_results=3)

        provider_event = next(
            (payload for event_name, payload in captured_calls if event_name == "provider.search.result"),
            None,
        )
        self.assertIsNotNone(provider_event)
        assert provider_event is not None
        self.assertEqual(provider_event["results"][0]["source_engines"], ["searxng-engine-a"])
        self.assertEqual(provider_event["results"][0]["raw_score"], 0.91)

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
