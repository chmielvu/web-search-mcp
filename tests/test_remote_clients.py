"""Tests for CamoufoxClient and Crawl4AIClient in remote_clients.py."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from kindly_web_search_mcp_server.content.remote_clients import (
    CamoufoxClient,
)

BASE_URL = "http://127.0.0.1:3000"


def _decode_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


async def _camoufox_client(handler) -> CamoufoxClient:
    """Build a CamoufoxClient with an httpx.MockTransport.

    The caller must await client.close() after use.
    """
    transport = httpx.MockTransport(handler)
    mock_http = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    client = CamoufoxClient(BASE_URL)
    await client._http.aclose()
    client._http = mock_http
    return client


class TestGetCamoufoxClient(unittest.IsolatedAsyncioTestCase):
    """Tests for get_camoufox_client singleton."""

    async def asyncTearDown(self) -> None:
        from kindly_web_search_mcp_server.content import remote_clients

        if remote_clients._camoufox_client is not None:
            await remote_clients._camoufox_client.close()
        remote_clients._camoufox_client = None

    async def test_returns_none_when_unconfigured(self) -> None:
        with patch("kindly_web_search_mcp_server.settings.settings") as mock_settings:
            mock_settings.camoufox_base_url = ""
            from kindly_web_search_mcp_server.content.remote_clients import (
                get_camoufox_client,
            )

            client = get_camoufox_client()
            self.assertIsNone(client)


class TestCamoufoxClient(unittest.IsolatedAsyncioTestCase):
    """Tests for CamoufoxClient.fetch_html and health_check."""

    async def test_returns_html_on_success(self) -> None:
        request_body = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_body
            request_body = _decode_body(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html><body>Hello</body></html>",
            )

        client = await _camoufox_client(handler)
        try:
            result = await client.fetch_html("http://example.com")
        finally:
            await client.close()

        self.assertEqual(result, "<html><body>Hello</body></html>")
        self.assertIsNotNone(request_body)
        self.assertEqual(request_body["url"], "http://example.com")
        self.assertEqual(
            request_body["gotoOptions"], {"waitUntil": "networkidle", "timeout": 15000}
        )

    async def test_raises_on_non_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad request")

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with self.assertRaises(CamoufoxClientError) as ctx:
                await client.fetch_html("http://example.com")
            self.assertIn("400", str(ctx.exception))
            self.assertFalse(ctx.exception.retryable)
        finally:
            await client.close()

    async def test_raises_on_non_html_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"error":"nope"}',
            )

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with self.assertRaises(CamoufoxClientError) as ctx:
                await client.fetch_html("http://example.com")
            self.assertIn("non-HTML", str(ctx.exception))
            self.assertFalse(ctx.exception.retryable)
        finally:
            await client.close()

    async def test_raises_on_empty_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"")

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with self.assertRaises(CamoufoxClientError) as ctx:
                await client.fetch_html("http://example.com")
            self.assertIn("empty", str(ctx.exception).lower())
            self.assertTrue(ctx.exception.retryable)
        finally:
            await client.close()

    async def test_raises_on_oversize_response(self) -> None:
        oversize = b"x" * (8 * 1024 * 1024 + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=oversize)

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with self.assertRaises(CamoufoxClientError) as ctx:
                await client.fetch_html("http://example.com")
            self.assertIn("8 MiB", str(ctx.exception))
            self.assertFalse(ctx.exception.retryable)
        finally:
            await client.close()

    async def test_retries_503(self) -> None:
        call_count = 0
        sleep_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(503, text="cold start")
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html>OK</html>"
            )

        client = await _camoufox_client(handler)
        try:
            with patch.object(
                asyncio, "sleep", AsyncMock(side_effect=lambda s: sleep_calls.append(s))
            ):
                result = await client.fetch_html("http://example.com")
        finally:
            await client.close()

        self.assertEqual(result, "<html>OK</html>")
        self.assertEqual(call_count, 3)
        self.assertEqual(len(sleep_calls), 2)
        self.assertEqual(sleep_calls, [2.0, 4.0])

    async def test_raises_after_503_retry_exhausted(self) -> None:
        call_count = 0
        sleep_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, text="still cold")

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with patch.object(
                asyncio, "sleep", AsyncMock(side_effect=lambda s: sleep_calls.append(s))
            ):
                with self.assertRaises(CamoufoxClientError) as ctx:
                    await client.fetch_html("http://example.com")
            self.assertIn("503", str(ctx.exception))
            self.assertTrue(ctx.exception.retryable)
        finally:
            await client.close()

        self.assertEqual(call_count, 3)
        self.assertEqual(sleep_calls, [2.0, 4.0])

    async def test_raises_on_transport_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("connect timed out")

        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClientError

        client = await _camoufox_client(handler)
        try:
            with self.assertRaises(CamoufoxClientError) as ctx:
                await client.fetch_html("http://example.com")
            self.assertIn("timed out", str(ctx.exception).lower())
            self.assertTrue(ctx.exception.retryable)
        finally:
            await client.close()

    async def test_health_check_caches_result(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        mock_http = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
        from kindly_web_search_mcp_server.content.remote_clients import CamoufoxClient

        client = CamoufoxClient(BASE_URL, health_cache_seconds=30.0)
        await client._http.aclose()
        client._http = mock_http
        try:
            self.assertTrue(await client.health_check())
            self.assertTrue(await client.health_check())
            self.assertEqual(call_count, 1)
        finally:
            await client.close()

    async def test_health_check_returns_false_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.RequestError("connection refused")

        client = await _camoufox_client(handler)
        try:
            self.assertFalse(await client.health_check())
        finally:
            await client.close()
