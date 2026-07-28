from __future__ import annotations

import asyncio
import json
import httpx
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _run_async(awaitable):
    return asyncio.run(awaitable)


class TestBrightDataSearchIntegration(unittest.TestCase):
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_brightdata_default_uses_google(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[
                WebSearchResult(title="Google Hit", link="https://g.com", snippet="from google"),
            ]
        )
        bing_mock = AsyncMock(
            return_value=[WebSearchResult(title="Bing Hit", link="https://b.com", snippet="b")]
        )
        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.run_provider",
                run_provider_mock,
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata._search_bing", bing_mock
            ),
        ):
            results = _run_async(search_brightdata("test", num_results=5))
        bing_mock.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Google Hit")

    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_news_skips_bing(self, *_):
        from kindly_web_search_mcp_server.models import WebSearchResult
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        run_provider_mock = AsyncMock(
            return_value=[WebSearchResult(title="News Hit", link="https://n.com", snippet="news")]
        )
        with patch(
            "kindly_web_search_mcp_server.search.providers.brightdata.run_provider",
            run_provider_mock,
        ):
            results = _run_async(search_brightdata("test", num_results=5, search_type="news"))
        self.assertEqual(len(results), 1)

    def test_search_returns_empty_for_blank_query(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("   ", num_results=5)), [])

    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
        return_value="test-key",
    )
    @patch(
        "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
        return_value={"zone": "test", "format": "raw"},
    )
    def test_search_returns_empty_when_no_results(self, *_):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.run_provider",
                AsyncMock(return_value=[]),
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata._search_bing",
                AsyncMock(return_value=[]),
            ),
        ):
            results = _run_async(search_brightdata("test", num_results=5))
        self.assertEqual(results, [])

    def test_search_returns_empty_for_zero_results(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        self.assertEqual(_run_async(search_brightdata("test", num_results=0)), [])

    def test_search_bing_propagates_cancellation(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import _search_bing

        class _CancelledClient:
            async def post(self, *args, **kwargs):
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            _run_async(_search_bing("test", 5, _CancelledClient(), "key", {}, {}, "us", "en"))

    def test_search_google_propagates_provider_error(self):
        from kindly_web_search_mcp_server.search.providers.base import ProviderRequestError
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        async def probe() -> None:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "temporary"})
            )
            async with httpx.AsyncClient(transport=transport) as client:
                await search_brightdata("test", num_results=5, http_client=client)

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
                return_value="test-key",
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
                return_value={"zone": "test", "format": "raw"},
            ),
            self.assertRaises(ProviderRequestError),
        ):
            _run_async(probe())

    def test_search_bing_propagates_provider_error(self):
        from kindly_web_search_mcp_server.search.providers.base import ProviderRequestError
        from kindly_web_search_mcp_server.search.providers.brightdata import _search_bing

        async def probe() -> None:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "temporary"})
            )
            async with httpx.AsyncClient(transport=transport) as client:
                await _search_bing(
                    "test",
                    5,
                    client,
                    "test-key",
                    {"zone": "test", "format": "raw"},
                    {},
                    "us",
                    "en",
                )

        with self.assertRaises(ProviderRequestError):
            _run_async(probe())

    def test_google_paginates_with_a_bounded_result_budget(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json.loads(request.content)
            target = httpx.URL(payload["url"])
            start = int(target.params.get("start", "0"))
            count = 10 if start == 0 else 5
            organic = [
                {
                    "title": f"Result {start + index}",
                    "link": f"https://example.com/{start + index}",
                    "description": "result",
                }
                for index in range(count)
            ]
            return httpx.Response(200, json={"organic": organic})

        async def probe() -> list:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await search_brightdata("test", num_results=15, http_client=client)

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
                return_value="test-key",
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
                return_value={"zone": "test", "format": "raw"},
            ),
        ):
            results = _run_async(probe())

        self.assertEqual(len(results), 15)
        self.assertEqual(len(requests), 2)
        first_payload = json.loads(requests[0].content)
        second_payload = json.loads(requests[1].content)
        self.assertIn("start=0", first_payload["url"])
        self.assertIn("start=10", second_payload["url"])
        self.assertNotIn("data_format", first_payload)

    def test_google_uses_current_parsed_light_fast_path_for_top_ten(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Fast result",
                            "link": "https://example.com/fast",
                            "description": "result",
                        }
                    ]
                },
            )

        async def probe() -> list:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await search_brightdata("test", num_results=5, http_client=client)

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
                return_value="test-key",
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
                return_value={"zone": "test", "format": "raw"},
            ),
        ):
            results = _run_async(probe())

        self.assertEqual(len(results), 1)
        self.assertEqual(captured[0]["data_format"], "parsed_light")

    def test_http_errors_preserve_brightdata_diagnostics(self):
        from kindly_web_search_mcp_server.search.providers.base import ProviderRequestError
        from kindly_web_search_mcp_server.search.providers.brightdata import search_brightdata

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"Retry-After": "0", "x-brd-err-msg": "rate limited"},
                json={"error": "too many requests"},
            )

        async def probe() -> None:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                await search_brightdata("test", num_results=5, http_client=client)

        with (
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.get_brightdata_api_key",
                return_value="test-key",
            ),
            patch(
                "kindly_web_search_mcp_server.search.providers.brightdata.resolve_payload_base",
                return_value={"zone": "test", "format": "raw"},
            ),
            self.assertRaises(ProviderRequestError) as raised,
        ):
            _run_async(probe())

        metadata = raised.exception.metadata
        self.assertEqual(metadata.http_status, 429)
        self.assertEqual(metadata.error_type, "rate_limit")
        self.assertEqual(metadata.response_meta["retry_after"], "0")
        self.assertEqual(metadata.response_meta["x_brd_err_msg"], "rate limited")

    def test_yandex_request_uses_bounded_http_timeout(self):
        from kindly_web_search_mcp_server.search.providers.brightdata import _search_yandex

        class RecordingClient:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            async def post(self, *args: object, **kwargs: object) -> httpx.Response:
                self.kwargs = kwargs
                return httpx.Response(
                    200,
                    text="",
                    request=httpx.Request("POST", "https://api.brightdata.com/request"),
                )

        client = RecordingClient()
        _run_async(
            _search_yandex(
                "test",
                num_results=5,
                http_client=client,
                payload_base={"zone": "test", "format": "raw"},
                req_headers={},
                yandex_region=None,
                language="en",
            )
        )
        self.assertIsInstance(client.kwargs["timeout"], httpx.Timeout)
