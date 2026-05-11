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
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker, _budget

        # Reset state before test
        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()
        _budget.reset()

        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)
        os.environ["SEARXNG_BASE_URL"] = "https://searx.example.org"

        with patch(
            "kindly_web_search_mcp_server.search.search_searxng", new_callable=AsyncMock
        ) as mock_searxng:
            mock_searxng.return_value = [
                WebSearchResult(title="X", link="https://example.com", snippet="S", page_content="")
            ]
            out = await search_web("q", num_results=1)

        self.assertEqual(out[0].title, "X")
        mock_searxng.assert_awaited()

    async def test_uses_tavily_when_only_tavily_key(self) -> None:
        """Tavily is used when SearXNG not configured."""
        from kindly_web_search_mcp_server.search import search_web

        os.environ.pop("SEARXNG_BASE_URL", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)
        os.environ["TAVILY_API_KEY"] = "tvly_test"

        with patch(
            "kindly_web_search_mcp_server.search.search_tavily", new_callable=AsyncMock
        ) as mock_tavily:
            mock_tavily.return_value = [
                WebSearchResult(title="T", link="https://example.com", snippet="S", page_content="")
            ]

            out = await search_web("q", num_results=1)

        self.assertEqual(len(out), 1)
        mock_tavily.assert_awaited()

    async def test_concurrent_providers_with_rrf_merge(self) -> None:
        """Multiple providers run concurrently, results merged via RRF."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker, _budget

        # Reset state before test
        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()
        _budget.reset()

        os.environ["SEARXNG_BASE_URL"] = "https://searx.example.org"
        os.environ["TAVILY_API_KEY"] = "tvly_test"
        os.environ["BRAVE_API_KEY"] = "brave_test"
        os.environ.pop("JINA_API_KEY", None)

        with (
            patch("kindly_web_search_mcp_server.search.search_searxng", new_callable=AsyncMock) as mock_searxng,
            patch("kindly_web_search_mcp_server.search.search_tavily", new_callable=AsyncMock) as mock_tavily,
            patch("kindly_web_search_mcp_server.search.search_brave", new_callable=AsyncMock) as mock_brave,
        ):
            # Same URL appears in multiple providers - RRF should dedup
            mock_searxng.return_value = [
                WebSearchResult(title="S1", link="https://shared.com", snippet="S", page_content="")
            ]
            mock_tavily.return_value = [
                WebSearchResult(title="T1", link="https://shared.com", snippet="T", page_content="")
            ]
            mock_brave.return_value = [
                WebSearchResult(title="B1", link="https://unique.com", snippet="B", page_content="")
            ]
            out = await search_web("q", num_results=5)

        # RRF dedup: shared.com appears once (higher score), unique.com appears
        self.assertEqual(len(out), 2)
        mock_searxng.assert_awaited()
        mock_tavily.assert_awaited()
        mock_brave.assert_awaited()

    async def test_circuit_breaker_opens_on_failures(self) -> None:
        """Circuit breaker opens after 3 consecutive failures."""
        from kindly_web_search_mcp_server.search import search_web, _circuit_breaker

        os.environ["SEARXNG_BASE_URL"] = "https://searx.example.org"
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)

        # Reset circuit breaker state
        _circuit_breaker._failures.clear()
        _circuit_breaker._opened_at.clear()

        with patch(
            "kindly_web_search_mcp_server.search.search_searxng", new_callable=AsyncMock
        ) as mock_searxng:
            # Fail 3 times
            mock_searxng.side_effect = httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://searx.example.org/search"),
                response=httpx.Response(500),
            )

            # After 3 failures, circuit breaker should open
            for _ in range(3):
                try:
                    await search_web("q", num_results=1)
                except:
                    pass

        # Circuit breaker should now be open
        self.assertTrue(_circuit_breaker.is_open("searxng"))

    async def test_raises_when_no_provider_configured(self) -> None:
        from kindly_web_search_mcp_server.search import WebSearchProviderError, search_web

        os.environ.pop("SEARXNG_BASE_URL", None)
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("JINA_API_KEY", None)

        with self.assertRaises(WebSearchProviderError):
            await search_web("q", num_results=1)


if __name__ == "__main__":
    unittest.main()