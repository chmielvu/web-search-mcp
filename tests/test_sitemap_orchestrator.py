"""Tests for Tavily-only sitemap orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch


class TestGenerateSitemapOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_tavily_map_results(self) -> None:
        tavily_result = {
            "base_url": "docs.tavily.com",
            "results": [
                "https://docs.tavily.com/welcome",
                "https://docs.tavily.com/documentation/api-credits",
            ],
            "response_time": 1.23,
        }

        with patch(
            "kindly_web_search_mcp_server.content.sitemap.map_site",
            new=AsyncMock(return_value=tavily_result),
        ) as map_mock:
            from kindly_web_search_mcp_server.content.sitemap import generate_sitemap

            result = await generate_sitemap(
                "https://docs.tavily.com",
                instructions="Map the docs site",
                max_depth=2,
                max_breadth=10,
                limit=25,
                select_paths=["/documentation/.*"],
                select_domains=["^docs\\.tavily\\.com$"],
                exclude_paths=["/private/.*"],
                exclude_domains=["^private\\.tavily\\.com$"],
                allow_external=False,
            )

        self.assertEqual(result, tavily_result)
        map_mock.assert_awaited_once()

    async def test_returns_empty_tavily_result_as_is(self) -> None:
        empty_result = {"base_url": "docs.tavily.com", "results": []}

        with patch(
            "kindly_web_search_mcp_server.content.sitemap.map_site",
            new=AsyncMock(return_value=empty_result),
        ) as map_mock:
            from kindly_web_search_mcp_server.content.sitemap import generate_sitemap

            result = await generate_sitemap("https://docs.tavily.com")

        self.assertEqual(result, empty_result)
        self.assertEqual(result["results"], [])
        map_mock.assert_awaited_once()

    async def test_forwards_all_options_to_tavily(self) -> None:
        tavily_result = {"base_url": "docs.tavily.com", "results": ["/welcome"]}

        with patch(
            "kindly_web_search_mcp_server.content.sitemap.map_site",
            new=AsyncMock(return_value=tavily_result),
        ) as map_mock:
            from kindly_web_search_mcp_server.content.sitemap import generate_sitemap

            result = await generate_sitemap(
                "https://docs.tavily.com",
                instructions="Map docs",
                max_depth=2,
                max_breadth=10,
                limit=25,
                select_paths=["/docs/.*"],
                select_domains=["^docs\\.tavily\\.com$"],
                exclude_paths=["/private/.*"],
                exclude_domains=["^private\\.tavily\\.com$"],
                allow_external=True,
            )

        self.assertEqual(result, tavily_result)
        map_mock.assert_awaited_once_with(
            "https://docs.tavily.com",
            instructions="Map docs",
            max_depth=2,
            max_breadth=10,
            limit=25,
            select_paths=["/docs/.*"],
            select_domains=["^docs\\.tavily\\.com$"],
            exclude_paths=["/private/.*"],
            exclude_domains=["^private\\.tavily\\.com$"],
            allow_external=True,
        )

    async def test_propagates_tavily_error(self) -> None:
        with patch(
            "kindly_web_search_mcp_server.content.sitemap.map_site",
            new=AsyncMock(side_effect=RuntimeError("Tavily unavailable")),
        ):
            from kindly_web_search_mcp_server.content.sitemap import generate_sitemap

            with self.assertRaises(RuntimeError):
                await generate_sitemap("https://docs.tavily.com")
