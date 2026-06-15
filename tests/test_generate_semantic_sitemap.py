"""Tests for the generate_semantic_sitemap MCP tool registration and wiring."""

from __future__ import annotations

import unittest


class TestToolCatalog(unittest.TestCase):
    def test_tool_registered_in_catalog(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG, tool_kwargs

        self.assertIn("generate_semantic_sitemap", TOOL_CATALOG)
        kwargs = tool_kwargs("generate_semantic_sitemap")
        self.assertIn("tags", kwargs)
        self.assertIn("annotations", kwargs)

    def test_tool_is_expensive(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG

        entry = TOOL_CATALOG["generate_semantic_sitemap"]
        self.assertTrue(entry.expensive)

    def test_tool_in_research_and_full_profiles(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG

        entry = TOOL_CATALOG["generate_semantic_sitemap"]
        self.assertIn("research", entry.profiles)
        self.assertIn("full", entry.profiles)


if __name__ == "__main__":
    unittest.main()
