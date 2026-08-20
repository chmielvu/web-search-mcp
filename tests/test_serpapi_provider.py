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
        from kindly_web_search_mcp_server.settings import settings

        search_mock.return_value = []
        with patch.object(settings, "serpapi_enabled", True), \
             patch.object(settings, "serpapi_disabled_engines", ("google", "baidu")), \
             patch.object(settings, "disabled_providers", ()):
            asyncio.run(search_serpapi("q", num_results=5, engine="naver"))
            self.assertEqual(search_mock.await_args.args[1], "naver")

    @patch(
        "kindly_web_search_mcp_server.search.providers.serpapi._get_serpapi_api_key",
        return_value="key",
    )
    def test_disabled_engines_raise_config_error(self, *_):
        import asyncio
        from kindly_web_search_mcp_server.search.providers.serpapi import (
            SerpApiConfigError,
            search_serpapi,
        )
        from kindly_web_search_mcp_server.settings import settings

        # By default, all serpapi engines are disabled
        with self.assertRaises(SerpApiConfigError):
            asyncio.run(search_serpapi("q", num_results=5))

        # Even when serpapi is enabled, google and baidu remain disabled
        with patch.object(settings, "serpapi_enabled", True), \
             patch.object(settings, "serpapi_disabled_engines", ("google", "baidu")), \
             patch.object(settings, "disabled_providers", ()):
            for disabled_engine in ("baidu", "google", "BAIDU", "Google"):
                with self.assertRaises(SerpApiConfigError):
                    asyncio.run(search_serpapi("q", num_results=5, engine=disabled_engine))

    def test_get_engines_returns_empty_when_all_disabled(self):
        from kindly_web_search_mcp_server.search.providers.serpapi import _get_engines

        # Default configuration: all disabled
        engines = _get_engines()
        self.assertEqual(engines, [])
    def test_get_engines_filters_disabled_engines_when_enabled(self):
        from kindly_web_search_mcp_server.search.providers.serpapi import _get_engines
        from kindly_web_search_mcp_server.settings import settings

        with patch.object(settings, "serpapi_enabled", True), \
             patch.object(settings, "serpapi_disabled_engines", ("google", "baidu")), \
             patch.object(settings, "disabled_providers", ()):
            with patch.dict("os.environ", {"SERPAPI_ENGINES": "yahoo,baidu,google,naver"}):
                engines = _get_engines()
                self.assertEqual(engines, ["yahoo", "naver"])

    def test_get_engines_respects_custom_disabled_engines(self):
        from kindly_web_search_mcp_server.search.providers.serpapi import _get_engines
        from kindly_web_search_mcp_server.settings import settings

        with patch.object(settings, "serpapi_enabled", True), \
             patch.object(settings, "serpapi_disabled_engines", ("naver",)), \
             patch.object(settings, "disabled_providers", ()):
            with patch.dict("os.environ", {"SERPAPI_ENGINES": "yahoo,naver"}):
                engines = _get_engines()
                self.assertEqual(engines, ["yahoo"])

    def test_get_engines_respects_disabled_providers_format(self):
        from kindly_web_search_mcp_server.search.providers.serpapi import _get_engines
        from kindly_web_search_mcp_server.settings import settings

        with patch.object(settings, "serpapi_enabled", True), \
             patch.object(settings, "serpapi_disabled_engines", ()), \
             patch.object(settings, "disabled_providers", ("serpapi_naver",)):
            with patch.dict("os.environ", {"SERPAPI_ENGINES": "yahoo,naver"}):
                engines = _get_engines()
                self.assertEqual(engines, ["yahoo"])
