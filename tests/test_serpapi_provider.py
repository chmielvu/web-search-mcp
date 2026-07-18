from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestSerpApiEngineOverride(unittest.TestCase):
    @patch(
        "kindly_web_search_mcp_server.search.providers.serpapi._get_serpapi_api_key",
        return_value="key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.serpapi._search_one_engine",
        new_callable=AsyncMock,
    )
    def test_engine_parameter_overrides_config(self, search_mock, *_):
        import asyncio

        from kindly_web_search_mcp_server.search.providers.serpapi import search_serpapi

        search_mock.return_value = []
        asyncio.run(search_serpapi("q", num_results=5, engine="baidu"))
        self.assertEqual(search_mock.await_args.args[1], "baidu")
