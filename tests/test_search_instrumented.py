from __future__ import annotations

from typing import Any
import unittest
from unittest.mock import patch

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search import (
    ProviderConfig,
    search_single_query,
)


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
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        with patch(
            "kindly_web_search_mcp_server.search.resolve_providers_for_search",
            return_value=[config],
        ):
            results = await search_single_query("FastMCP docs", num_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].link, "https://example.com/fastmcp")
        self.assertEqual(results[0].providers, ["searxng"])

    async def test_instrumented_search_persists_raw_provider_results(self) -> None:
        config = ProviderConfig(
            name="searxng",
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
                "kindly_web_search_mcp_server.search.resolve_providers_for_search",
                return_value=[config],
            ),
            patch(
                "kindly_web_search_mcp_server.analytics.duckdb_store.append_event",
                side_effect=_capture,
            ),
        ):
            await search_single_query("FastMCP docs", num_results=3)

        provider_event = next(
            (
                payload
                for event_name, payload in captured_calls
                if event_name == "provider.search.result"
            ),
            None,
        )
        self.assertIsNotNone(provider_event)
        assert provider_event is not None
        self.assertEqual(provider_event["results"][0]["source_engines"], ["searxng-engine-a"])
        self.assertEqual(provider_event["results"][0]["raw_score"], 0.91)

    async def test_instrumented_search_logs_provider_task_crashes(self) -> None:
        config = ProviderConfig(
            name="searxng",
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        with (
            patch(
                "kindly_web_search_mcp_server.search.resolve_providers_for_search",
                return_value=[config],
            ),
            patch("asyncio.gather", return_value=[RuntimeError("task crashed")]),
        ):
            results = await search_single_query("FastMCP docs", num_results=3)

        self.assertEqual(results, [])

    async def test_instrumented_search_forwards_provider_options(self) -> None:
        from kindly_web_search_mcp_server.search.context import SearchContext
        from kindly_web_search_mcp_server.search.options import SearchOptions
        from kindly_web_search_mcp_server.search.profiles.models import SearchProfile
        from kindly_web_search_mcp_server.search.provider_plan import (
            build_provider_execution_plan,
        )

        config = ProviderConfig(
            name="searxng",
            env_key="",
            search_fn=_fake_provider,
            is_free=True,
            requires_key=False,
        )

        captured: dict[str, Any] = {}

        async def _capture_original(
            provider_name: str,
            provider_fn: Any,
            query: str,
            num_results: int,
            http_client: object,
            search_options: object | None = None,
            budget: object | None = None,
            provider_arguments: dict[str, object] | None = None,
        ) -> list[WebSearchResult]:
            captured["provider_name"] = provider_name
            captured["provider_arguments"] = provider_arguments
            captured["search_options"] = search_options
            return [
                WebSearchResult(
                    title=f"Result for {query}",
                    link="https://example.com/fastmcp",
                    snippet=f"limit={num_results}",
                    source_engines=["searxng-engine-a"],
                    raw_score=0.91,
                )
            ]

        with (
            patch(
                "kindly_web_search_mcp_server.search.resolve_providers_for_search",
                return_value=[config],
            ),
            patch(
                "kindly_web_search_mcp_server.search.query_execution._search_single_provider",
                side_effect=_capture_original,
                ),
            ):
            profile = SearchProfile(
                name="general",
                provider_weights={"searxng": 1.0},
                provider_names=("searxng",),
                provider_arguments={"searxng": {"country": "us"}},
            )
            context = SearchContext(
                raw_query="FastMCP docs",
                normalized_query="FastMCP docs",
                research_goal=None,
                session_id="session-1",
                intent="general",
                confidence=0.9,
                should_decompose=False,
                rationale="clear request",
                entities=(),
                must_keep_terms=(),
                providers=("searxng",),
                num_results=3,
                search_options=SearchOptions(),
                profile_name="general",
            )
            provider_plan = build_provider_execution_plan(
                profile=profile,
                context=context,
                public_options=context.search_options,
            )
            results = await search_single_query(
                "FastMCP docs",
                num_results=3,
                provider_plan=provider_plan,
                provider_options_by_name=provider_plan.options.bundles,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(captured["provider_name"], "searxng")
        self.assertEqual(captured["provider_arguments"], {"country": "us"})
        assert captured["search_options"] is context.search_options
