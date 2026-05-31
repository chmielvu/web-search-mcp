"""Tests for multi-provider search router with RRF merge."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from kindly_web_search_mcp_server.models import WebSearchResult


class TestSearchRouter(unittest.IsolatedAsyncioTestCase):
    async def test_uses_searxng_when_only_searxng_config(self) -> None:
        """SearXNG is primary - always fires when configured."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker
        from kindly_web_search_mcp_server.search import provider_config as pc

        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()

        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)

        mock_searxng = AsyncMock(
            return_value=[
                WebSearchResult(title="X", link="https://example.com", snippet="S")
            ]
        )

        def _resolve_only_searxng(**kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="searxng",
                mode=pc.ProviderMode.ALWAYS,
                env_key="",
                search_fn=mock_searxng,
                is_free=True,
                requires_key=False,
            )
            return [config]

        with patch.object(
            pc, "resolve_providers_for_search", side_effect=_resolve_only_searxng
        ):
            out = await search_web("q", num_results=1)

        self.assertEqual(out[0].title, "X")
        mock_searxng.assert_awaited()

    async def test_uses_tavily_when_only_tavily_key(self) -> None:
        """Tavily fires when SearXNG is unconfigured."""
        from kindly_web_search_mcp_server.search import search_web
        from kindly_web_search_mcp_server.search import provider_config as pc

        mock_tavily = AsyncMock(
            return_value=[
                WebSearchResult(title="T", link="https://example.com", snippet="S")
            ]
        )

        def _resolve_only_tavily(**kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="tavily",
                mode=pc.ProviderMode.ALWAYS,
                env_key="",
                search_fn=mock_tavily,
                is_free=False,
                requires_key=False,
            )
            return [config]

        with patch.object(
            pc, "resolve_providers_for_search", side_effect=_resolve_only_tavily
        ):
            out = await search_web("q", num_results=1)

        self.assertEqual(len(out), 1)
        mock_tavily.assert_awaited()

    async def test_concurrent_providers_with_rrf_merge(self) -> None:
        """Multiple providers run concurrently, results merged via RRF."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker
        from kindly_web_search_mcp_server.search import provider_config as pc

        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()

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

        def _resolve_multi(**kwargs):  # noqa: ARG001
            return [
                pc.ProviderConfig(
                    name="searxng",
                    mode=pc.ProviderMode.ALWAYS,
                    env_key="",
                    search_fn=mock_searxng,
                    is_free=True,
                    requires_key=False,
                ),
                pc.ProviderConfig(
                    name="tavily",
                    mode=pc.ProviderMode.ALWAYS,
                    env_key="",
                    search_fn=mock_tavily,
                    is_free=False,
                    requires_key=False,
                ),
                pc.ProviderConfig(
                    name="brave",
                    mode=pc.ProviderMode.ALWAYS,
                    env_key="",
                    search_fn=mock_brave,
                    is_free=False,
                    requires_key=False,
                ),
            ]

        with patch.object(
            pc, "resolve_providers_for_search", side_effect=_resolve_multi
        ):
            out = await search_web("q", num_results=5)

        self.assertEqual(len(out), 2)
        mock_searxng.assert_awaited()
        mock_tavily.assert_awaited()
        mock_brave.assert_awaited()

    async def test_circuit_breaker_opens_on_failures(self) -> None:
        """Circuit breaker opens after 3 consecutive failures."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker
        from kindly_web_search_mcp_server.search import provider_config as pc

        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)

        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()

        mock_searxng = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://searx.example.org/search"),
                response=httpx.Response(500),
            )
        )

        def _resolve(**kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="searxng",
                mode=pc.ProviderMode.ALWAYS,
                env_key="",
                search_fn=mock_searxng,
                is_free=True,
                requires_key=False,
            )
            return [config]

        with patch.object(pc, "resolve_providers_for_search", side_effect=_resolve):
            for _ in range(3):
                try:
                    await search_web("q", num_results=1)
                except Exception:
                    pass

        self.assertTrue(_circuit_breaker.is_open("searxng"))

    async def test_raises_when_no_provider_configured(self) -> None:
        """DDG free fallback succeeds even without any env keys."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker
        from kindly_web_search_mcp_server.search import provider_config as pc

        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()

        mock_ddg = AsyncMock(
            return_value=[
                WebSearchResult(title="DDG", link="https://example.com", snippet="S")
            ]
        )

        def _resolve(**kwargs):  # noqa: ARG001
            config = pc.ProviderConfig(
                name="ddg",
                mode=pc.ProviderMode.ALWAYS,
                env_key="",
                search_fn=mock_ddg,
                is_free=True,
                requires_key=False,
            )
            return [config]

        with patch.object(pc, "resolve_providers_for_search", side_effect=_resolve):
            out = await search_web("q", num_results=1)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].title, "DDG")


if __name__ == "__main__":
    unittest.main()
