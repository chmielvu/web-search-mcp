from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import anyio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestLangSearchProvider(unittest.TestCase):
    def test_config_error_on_empty_key(self) -> None:
        async def run() -> None:
            os.environ.pop("LANGSEARCH_API_KEY", None)
            from kindly_web_search_mcp_server.search.providers.langsearch import (
                LangSearchConfigError,
                _get_langsearch_api_key,
            )

            # Patch settings to force empty key
            import kindly_web_search_mcp_server.search.providers.langsearch as mod

            original = mod.settings.langsearch_api_key
            mod.settings.langsearch_api_key = ""
            try:
                with self.assertRaises(LangSearchConfigError):
                    _get_langsearch_api_key()
            finally:
                mod.settings.langsearch_api_key = original

        anyio.run(run)

    def test_search_langsearch_parses_results(self) -> None:
        async def run() -> None:
            os.environ["LANGSEARCH_API_KEY"] = "ls_test"

            from kindly_web_search_mcp_server.search.providers.langsearch import (
                search_langsearch,
            )

            payload = {
                "code": 200,
                "data": {
                    "_type": "SearchResponse",
                    "queryContext": {"originalQuery": "python asyncio"},
                    "webPages": {
                        "webSearchUrl": "https://langsearch.com/search?q=python+asyncio",
                        "totalEstimatedMatches": None,
                        "value": [
                            {
                                "id": "https://api.langsearch.com/v1/web-search#1",
                                "name": "asyncio — Asynchronous I/O",
                                "url": "https://docs.python.org/3/library/asyncio.html",
                                "displayUrl": "https://docs.python.org/3/library/asyncio.html",
                                "snippet": "asyncio is a library to write concurrent code...",
                                "datePublished": "2024-01-15",
                                "dateLastCrawled": "2024-06-01",
                            }
                        ],
                    },
                },
            }

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), "https://api.langsearch.com/v1/web-search")
                self.assertEqual(request.headers.get("authorization"), "Bearer ls_test")
                self.assertEqual(request.headers.get("content-type"), "application/json")
                body = request.read()
                import json

                payload_data = json.loads(body)
                self.assertEqual(payload_data["query"], "python asyncio")
                self.assertEqual(payload_data["freshness"], "noLimit")
                self.assertFalse(payload_data["summary"])
                self.assertEqual(payload_data["count"], 5)
                return httpx.Response(200, json=payload)

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                results = await search_langsearch(
                    "python asyncio", num_results=5, http_client=client
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "asyncio — Asynchronous I/O")
            self.assertEqual(results[0].link, "https://docs.python.org/3/library/asyncio.html")
            self.assertTrue(results[0].snippet)
            self.assertEqual(results[0].domain, "docs.python.org")
            self.assertEqual(results[0].published_date, "2024-01-15")

        anyio.run(run)

    def test_count_clamped_to_10(self) -> None:
        async def run() -> None:
            os.environ["LANGSEARCH_API_KEY"] = "ls_test"

            from kindly_web_search_mcp_server.search.providers.langsearch import (
                search_langsearch,
            )

            payload = {
                "code": 200,
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "name": f"Result {i}",
                                "url": f"https://example.com/{i}",
                                "snippet": "snippet",
                            }
                            for i in range(10)
                        ]
                    }
                },
            }

            def handler(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.read())
                # Even though num_results=50, count should be clamped to 10
                self.assertEqual(body["count"], 10)
                return httpx.Response(200, json=payload)

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                results = await search_langsearch("test", num_results=50, http_client=client)

            self.assertEqual(len(results), 10)

        anyio.run(run)

    def test_http_error_handling(self) -> None:
        async def run() -> None:
            os.environ["LANGSEARCH_API_KEY"] = "ls_test"

            from kindly_web_search_mcp_server.search.providers.langsearch import (
                search_langsearch,
            )
            from kindly_web_search_mcp_server.search.providers.base import ProviderRequestError

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(500, text="Internal Server Error")

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                # run_provider preserves status and wraps HTTP failures with metadata.
                with self.assertRaises(ProviderRequestError) as context:
                    await search_langsearch("test", num_results=5, http_client=client)
                self.assertEqual(context.exception.metadata.http_status, 500)
                self.assertEqual(context.exception.metadata.error_type, "upstream")

        anyio.run(run)

    def test_empty_query_returns_empty(self) -> None:
        async def run() -> None:
            os.environ["LANGSEARCH_API_KEY"] = "ls_test"

            from kindly_web_search_mcp_server.search.providers.langsearch import (
                search_langsearch,
            )

            results = await search_langsearch("   ", num_results=5)
            self.assertEqual(results, [])

        anyio.run(run)

    def test_catalog_round_trip(self) -> None:
        from kindly_web_search_mcp_server.search.provider_catalog import (
            PROVIDER_DEFINITIONS_LIST,
        )
        from kindly_web_search_mcp_server.search.provider_registry import (
            PROVIDER_ADAPTERS,
            PROVIDER_DEFINITIONS,
        )

        names = {d.name for d in PROVIDER_DEFINITIONS_LIST}
        self.assertIn("langsearch", names)
        self.assertIn("langsearch", PROVIDER_DEFINITIONS)
        self.assertIn("langsearch", PROVIDER_ADAPTERS)
        langsearch_def = PROVIDER_DEFINITIONS["langsearch"]
        self.assertEqual(langsearch_def.adapter_module, "providers.langsearch")
        self.assertEqual(langsearch_def.adapter_function, "search_langsearch")
        self.assertEqual(langsearch_def.all_of, ("LANGSEARCH_API_KEY",))


if __name__ == "__main__":
    unittest.main()
