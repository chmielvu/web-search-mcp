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

        with (
            patch(
                "kindly_web_search_mcp_server.content.sitemap.map_site",
                new=AsyncMock(return_value=tavily_result),
            ) as map_mock,
            patch(
                "kindly_web_search_mcp_server.content.sitemap.crawl_legacy_sitemap",
                new=AsyncMock(),
            ) as legacy_mock,
        ):
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
        legacy_mock.assert_not_awaited()

    async def test_falls_back_to_legacy_sitemap_when_tavily_empty(self) -> None:
        legacy_result = {
            "query_url": "https://docs.tavily.com",
            "pages": [{"url": "https://docs.tavily.com/welcome"}],
            "stats": {"pages_crawled": 1, "pages_failed": 0, "total_sections": 1},
        }

        with (
            patch(
                "kindly_web_search_mcp_server.content.sitemap.map_site",
                new=AsyncMock(return_value={"base_url": "docs.tavily.com", "results": []}),
            ),
            patch(
                "kindly_web_search_mcp_server.content.sitemap.crawl_legacy_sitemap",
                new=AsyncMock(return_value=legacy_result),
            ) as legacy_mock,
        ):
            from kindly_web_search_mcp_server.content.sitemap import generate_sitemap

            result = await generate_sitemap("https://docs.tavily.com", limit=10)

        self.assertEqual(result, legacy_result)
        legacy_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
