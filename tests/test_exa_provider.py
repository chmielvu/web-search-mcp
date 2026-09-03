from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import anyio
import httpx
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestExaProvider(unittest.TestCase):
    def test_search_exa_posts_native_search_payload_and_parses_results(self) -> None:
        async def run() -> None:
            os.environ["EXA_API_KEY"] = "exa_test"

            from kindly_web_search_mcp_server.search.options import SearchOptions
            from kindly_web_search_mcp_server.search.providers.exa import search_exa

            payload = {
                "results": [
                    {
                        "title": "Example semantic result",
                        "url": "https://example.com/result",
                        "publishedDate": "2026-01-02T00:00:00.000Z",
                        "highlights": ["A relevant semantic highlight."],
                    }
                ]
            }

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), "https://api.exa.ai/search")
                self.assertEqual(request.headers.get("x-api-key"), "exa_test")
                body = request.read()
                self.assertIn(b'"query":"semantic query"', body)
                self.assertIn(b'"numResults":1', body)
                return httpx.Response(200, json=payload)

            transport = httpx.MockTransport(handler)
            options = SearchOptions()
            async with httpx.AsyncClient(transport=transport) as client:
                results = await search_exa(
                    "semantic query",
                    num_results=1,
                    search_options=options,
                    http_client=client,
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Example semantic result")
            self.assertEqual(results[0].link, "https://example.com/result")
            self.assertEqual(results[0].published_date, "2026-01-02T00:00:00.000Z")
            self.assertEqual(results[0].snippet, "A relevant semantic highlight.")

        anyio.run(run)
    def test_search_exa_applies_intent_arguments_and_freshness(self) -> None:
        async def run() -> None:
            os.environ["EXA_API_KEY"] = "exa_test"

            from kindly_web_search_mcp_server.search.providers.exa import search_exa

            def handler(request: httpx.Request) -> httpx.Response:
                body = request.read()
                self.assertIn(b'"type":"auto"', body)
                self.assertIn(b'"category":"news"', body)
                self.assertIn(b'"startPublishedDate":"', body)
                self.assertIn(b'"moderation":true', body)
                return httpx.Response(200, json={"results": []})

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                await search_exa(
                    "semantic query",
                    num_results=5,
                    type="auto",
                    category="news",
                    freshness="week",
                    http_client=client,
                )

        anyio.run(run)

    def test_search_exa_merges_contents_arguments_into_contents(self) -> None:
        async def run() -> None:
            os.environ["EXA_API_KEY"] = "exa_test"

            from kindly_web_search_mcp_server.search.providers.exa import search_exa

            def handler(request: httpx.Request) -> httpx.Response:
                body = request.read()
                self.assertIn(b'"contents":{"highlights":true', body)
                self.assertIn(b'"maxAgeHours":0', body)
                self.assertIn(b'"livecrawlTimeout":12000', body)
                return httpx.Response(200, json={"results": []})

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                await search_exa(
                    "semantic query",
                    num_results=5,
                    maxAgeHours=0,
                    livecrawlTimeout=12000,
                    http_client=client,
                )

        anyio.run(run)

    def test_search_exa_rejects_unknown_provider_arguments(self) -> None:
        async def run() -> None:
            os.environ["EXA_API_KEY"] = "exa_test"

            from kindly_web_search_mcp_server.search.providers.exa import ExaError, search_exa

            with self.assertRaises(ExaError):
                await search_exa("semantic query", num_results=5, bogus_argument=True)

        anyio.run(run)

    def test_search_exa_moderation_defaults_true_but_override_allowed(self) -> None:
        async def run() -> None:
            os.environ["EXA_API_KEY"] = "exa_test"

            from kindly_web_search_mcp_server.search.providers.exa import search_exa

            seen: list[bytes] = []

            def handler(request: httpx.Request) -> httpx.Response:
                seen.append(request.read())
                return httpx.Response(200, json={"results": []})

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                await search_exa("semantic query", num_results=5, http_client=client)
                await search_exa(
                    "semantic query", num_results=5, moderation=False, http_client=client
                )

            self.assertEqual(len(seen), 2)
            self.assertIn(b'"moderation":true', seen[0])
            self.assertIn(b'"moderation":false', seen[1])

        anyio.run(run)

    def test_translate_exa_freshness_maps_vocabulary(self) -> None:
        from kindly_web_search_mcp_server.search.providers.exa import (
            ExaError,
            translate_exa_freshness,
        )

        windows = {"day": 86_400, "week": 604_800, "month": 2_592_000, "year": 31_536_000}
        for value, window in windows.items():
            result = translate_exa_freshness(value)
            assert result is not None
            self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z$")
            parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - parsed).total_seconds()
            self.assertAlmostEqual(elapsed, window, delta=60)
        self.assertIsNone(translate_exa_freshness(None))
        self.assertIsNone(translate_exa_freshness(""))
        with self.assertRaises(ExaError):
            translate_exa_freshness("decade")


if __name__ == "__main__":
    unittest.main()
