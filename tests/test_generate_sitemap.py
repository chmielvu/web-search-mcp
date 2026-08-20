"""Tests for the Generate Sitemap MCP tool registration and wiring."""

from __future__ import annotations

import unittest


class TestToolCatalog(unittest.TestCase):
    def test_tool_registered_in_catalog(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG, tool_kwargs

        self.assertIn("generate_sitemap", TOOL_CATALOG)
        self.assertNotIn("generate_semantic_sitemap", TOOL_CATALOG)
        kwargs = tool_kwargs("generate_sitemap")
        self.assertIn("tags", kwargs)
        self.assertIn("annotations", kwargs)

    def test_tool_is_expensive(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG

        entry = TOOL_CATALOG["generate_sitemap"]
        self.assertTrue(entry.expensive)

    def test_tool_in_research_and_full_profiles(self) -> None:
        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG

        entry = TOOL_CATALOG["generate_sitemap"]
        self.assertIn("regular", entry.profiles)
        self.assertIn("research", entry.profiles)
        self.assertIn("full", entry.profiles)

    def test_tool_is_background_capable(self) -> None:
        from fastmcp.server.tasks import TaskConfig

        from kindly_web_search_mcp_server.tools.catalog import TOOL_CATALOG, tool_kwargs

        entry = TOOL_CATALOG["generate_sitemap"]
        self.assertTrue(entry.task)
        kwargs = tool_kwargs("generate_sitemap")
        self.assertIsInstance(kwargs["task"], TaskConfig)
        self.assertEqual(kwargs["task"].mode, "optional")


if __name__ == "__main__":
    unittest.main()
