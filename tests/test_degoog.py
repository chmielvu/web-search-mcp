from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import anyio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestDeGoogParsing(unittest.TestCase):
    @staticmethod
    def _mock_settings(**overrides):
        """Return a mock settings object with DeGoog fields set."""
        from unittest.mock import MagicMock

        defaults = {
            "degoog_base_url": "http://localhost:4444",
            "degoog_timeout_seconds": 15.0,
            "degoog_engines": "",
        }
        defaults.update(overrides)
        mock_settings = MagicMock()
        for k, v in defaults.items():
            setattr(mock_settings, k, v)
        return mock_settings

    def test_search_degoog_raises_config_error_missing_url(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import (
                DeGoogConfigError,
                search_degoog,
            )

            mock_s = self._mock_settings(degoog_base_url="")
            with mock_patch(
                "kindly_web_search_mcp_server.search.degoog.settings", mock_s
            ):
                with self.assertRaises(DeGoogConfigError):
                    await search_degoog("q", num_results=1)

        anyio.run(run)

    def test_search_degoog_raises_config_error_invalid_url(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import (
                DeGoogConfigError,
                search_degoog,
            )

            mock_s = self._mock_settings(degoog_base_url="not a url")
            with mock_patch(
                "kindly_web_search_mcp_server.search.degoog.settings", mock_s
            ):
                with self.assertRaises(DeGoogConfigError):
                    await search_degoog("q", num_results=1)

        anyio.run(run)

    def test_search_degoog_empty_query(self) -> None:
        """Returns [] immediately without reading Settings."""
        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import search_degoog

            results = await search_degoog("", num_results=5)
            self.assertEqual(results, [])

        anyio.run(run)

    def test_search_degoog_parses_results(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import search_degoog

            payload = {
                "results": [
                    {
                        "title": "Welcome to Python.org",
                        "url": "https://www.python.org",
                        "snippet": "Python is a versatile programming language.",
                        "source": "Bing",
                        "score": 40,
                        "sources": ["Bing", "Brave Search", "DuckDuckGo"],
                    }
                ],
                "totalTime": 3000,
                "engineTimings": [{"name": "Bing", "time": 395, "resultCount": 10}],
            }

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                self.assertIn("/api/search", str(request.url))
                body = json.loads(request.content)
                self.assertEqual(body["query"], "python")
                return httpx.Response(200, json=payload)

            mock_s = self._mock_settings()
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    results = await search_degoog(
                        "python", num_results=10, http_client=client
                    )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Welcome to Python.org")
            self.assertEqual(results[0].link, "https://www.python.org")
            self.assertEqual(results[0].snippet, "Python is a versatile programming language.")
            self.assertEqual(results[0].source_engines, ["Bing", "Brave Search", "DuckDuckGo"])
            self.assertEqual(results[0].raw_score, 40.0)
            self.assertIn("degoog", results[0].providers)

        anyio.run(run)

    def test_search_degoog_passes_engine_list(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import search_degoog

            captured_body = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured_body.update(json.loads(request.content))
                return httpx.Response(200, json={"results": []})

            mock_s = self._mock_settings(degoog_engines="bing,duckduckgo")
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    await search_degoog("test", num_results=5, http_client=client)

            self.assertEqual(captured_body.get("engines"), ["bing", "duckduckgo"])

        anyio.run(run)

    def test_search_degoog_uses_content_fallback(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import search_degoog

            payload = {
                "results": [
                    {
                        "title": "Test",
                        "url": "https://example.com",
                        "content": "Fallback content field.",
                        "source": "Bing",
                        "score": 10,
                    }
                ]
            }

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=payload)

            mock_s = self._mock_settings()
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    results = await search_degoog("test", num_results=5, http_client=client)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].snippet, "Fallback content field.")

        anyio.run(run)

    def test_search_degoog_skips_malformed_items(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import search_degoog

            payload = {
                "results": [
                    {"title": "Missing url", "snippet": "x"},
                    {"title": "Bad url", "url": "not-a-url", "snippet": "x"},
                    {"title": "Missing snippet", "url": "https://example.com/"},
                    {"title": "Good", "url": "https://good.example/", "snippet": "ok"},
                ]
            }

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=payload)

            mock_s = self._mock_settings()
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    results = await search_degoog("q", num_results=10, http_client=client)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Good")

        anyio.run(run)

    def test_search_degoog_raises_on_http_error(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import DeGoogError, search_degoog

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(502, text="bad gateway")

            mock_s = self._mock_settings()
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    with self.assertRaises(DeGoogError) as ctx:
                        await search_degoog("q", num_results=1, http_client=client)
                    self.assertIn("502", str(ctx.exception))

        anyio.run(run)

    def test_search_degoog_raises_on_invalid_json(self) -> None:
        from unittest.mock import patch as mock_patch

        async def run() -> None:
            from kindly_web_search_mcp_server.search.degoog import DeGoogError, search_degoog

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, text="not json")

            mock_s = self._mock_settings()
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with mock_patch(
                    "kindly_web_search_mcp_server.search.degoog.settings", mock_s
                ):
                    with self.assertRaises(DeGoogError) as ctx:
                        await search_degoog("q", num_results=1, http_client=client)
                    self.assertIn("not valid JSON", str(ctx.exception))

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()
