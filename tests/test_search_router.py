"""Tests for multi-provider search router with RRF merge."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.provider_config import ProviderGroup


class TestSearchRouter(unittest.IsolatedAsyncioTestCase):
    async def test_uses_searxng_when_only_searxng_config(self) -> None:
        """SearXNG is primary - always fires when configured."""
        from kindly_web_search_mcp_server.search import search_single_query
        from kindly_web_search_mcp_server.search import provider_config as pc
        from kindly_web_search_mcp_server.search.provider_health import (
            reset_provider_health,
        )

        reset_provider_health()

        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)

        mock_searxng = AsyncMock(
            return_value=[
                WebSearchResult(title="X", link="https://example.com", snippet="S")
            ]
        )

        def _resolve_only_searxng(*args, **kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="searxng",
                env_key="",
                search_fn=mock_searxng,
                group=ProviderGroup.free,
                requires_key=False,
            )
            return [config]

        with patch(
            "kindly_web_search_mcp_server.search.query_execution.resolve_providers_for_search",
            side_effect=_resolve_only_searxng,
        ):
            out = await search_single_query("q", num_results=1)

        self.assertEqual(out[0].title, "X")
        mock_searxng.assert_awaited()

    async def test_uses_tavily_when_only_tavily_key(self) -> None:
        """Tavily fires when SearXNG is unconfigured."""
        from kindly_web_search_mcp_server.search import search_single_query
        from kindly_web_search_mcp_server.search import provider_config as pc

        mock_tavily = AsyncMock(
            return_value=[
                WebSearchResult(title="T", link="https://example.com", snippet="S")
            ]
        )

        def _resolve_only_tavily(*args, **kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="tavily",
                env_key="",
                search_fn=mock_tavily,
                group=ProviderGroup.serp_paid,
                requires_key=False,
            )
            return [config]

        with patch(
            "kindly_web_search_mcp_server.search.query_execution.resolve_providers_for_search",
            side_effect=_resolve_only_tavily,
        ):
            out = await search_single_query("q", num_results=1)

        self.assertEqual(len(out), 1)
        mock_tavily.assert_awaited()

    async def test_concurrent_providers_with_rrf_merge(self) -> None:
        """Multiple providers run concurrently, results merged via RRF."""
        from kindly_web_search_mcp_server.search import search_single_query
        from kindly_web_search_mcp_server.search import provider_config as pc
        from kindly_web_search_mcp_server.search.provider_health import (
            reset_provider_health,
        )

        reset_provider_health()

        mock_searxng = AsyncMock(
            return_value=[
                WebSearchResult(title="S1", link="https://shared.com", snippet="S")
            ]
        )
        mock_tavily = AsyncMock(
            return_value=[
                WebSearchResult(title="T1", link="https://shared.com", snippet="T")
            ]
        )
        mock_brave = AsyncMock(
            return_value=[
                WebSearchResult(title="B1", link="https://unique.com", snippet="B")
            ]
        )

        def _resolve_multi(*args, **kwargs):  # noqa: ARG001
            return [
                pc.ProviderConfig(
                    name="searxng",
                    env_key="",
                    search_fn=mock_searxng,
                    group=ProviderGroup.free,
                    requires_key=False,
                ),
                pc.ProviderConfig(
                    name="tavily",
                    env_key="",
                    search_fn=mock_tavily,
                    group=ProviderGroup.serp_paid,
                    requires_key=False,
                ),
                pc.ProviderConfig(
                    name="brave",
                    env_key="",
                    search_fn=mock_brave,
                    group=ProviderGroup.serp_paid,
                    requires_key=False,
                ),
            ]

        with patch(
            "kindly_web_search_mcp_server.search.query_execution.resolve_providers_for_search",
            side_effect=_resolve_multi,
        ):
            out = await search_single_query("q", num_results=5)

        self.assertEqual(len(out), 2)
        mock_searxng.assert_awaited()
        mock_tavily.assert_awaited()
        mock_brave.assert_awaited()

    async def test_circuit_breaker_opens_on_failures(self) -> None:
        """Circuit breaker opens after 3 consecutive failures."""
        from kindly_web_search_mcp_server.search.provider_health import (
            get_provider_health,
            reset_provider_health,
        )

        reset_provider_health()
        tracker = get_provider_health()

        # Mark 3 failures directly on the tracker.  Calling mark_failure_with_type
        # exercises the same circuit-breaker path that _search_single_provider
        # uses on provider errors, but avoids the slow search pipeline overhead
        # (OTLP span export, httpx client lifecycle, etc.) which can take 4-7 s
        # per call and cause the cooldown to expire before the assertion runs.
        for _ in range(3):
            tracker.mark_failure_with_type("searxng", error_type="HTTPStatusError")

        # After 3 failures, the provider should be unhealthy (circuit open)
        self.assertFalse(tracker.is_healthy("searxng"))

    async def test_raises_when_no_provider_configured(self) -> None:
        """DDG free fallback succeeds even without any env keys."""
        from kindly_web_search_mcp_server.search import search_single_query
        from kindly_web_search_mcp_server.search import provider_config as pc
        from kindly_web_search_mcp_server.search.provider_health import (
            reset_provider_health,
        )

        reset_provider_health()

        mock_ddg = AsyncMock(
            return_value=[
                WebSearchResult(title="DDG", link="https://example.com", snippet="S")
            ]
        )

        def _resolve(*args, **kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="ddg",
                env_key="",
                search_fn=mock_ddg,
                group=ProviderGroup.free,
                requires_key=False,
            )
            return [config]

        with patch(
            "kindly_web_search_mcp_server.search.query_execution.resolve_providers_for_search",
            side_effect=_resolve,
        ):
            out = await search_single_query("q", num_results=1)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "DDG")


if __name__ == "__main__":
    unittest.main()
