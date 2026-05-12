from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestJinaReader(unittest.IsolatedAsyncioTestCase):
    async def test_uses_no_key_first(self) -> None:
        from kindly_web_search_mcp_server.content.jina_reader import fetch_with_jina_reader

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=_FakeResponse(200, "content"))

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = fake_client
            content = await fetch_with_jina_reader("https://example.com")

        self.assertEqual(content, "content")
        self.assertEqual(fake_client.get.await_count, 1)
        args, kwargs = fake_client.get.await_args
        self.assertEqual(args[0], "https://r.jina.ai/https://example.com")
        self.assertNotIn("Authorization", kwargs["headers"])

    async def test_retries_with_key_on_429(self) -> None:
        from kindly_web_search_mcp_server.content.jina_reader import fetch_with_jina_reader

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(
            side_effect=[_FakeResponse(429, "rate limited"), _FakeResponse(200, "final")]
        )

        with patch("httpx.AsyncClient") as mock_client_cls, patch.dict(
            os.environ, {"JINA_API_KEY": "test-key"}, clear=False
        ):
            mock_client_cls.return_value.__aenter__.return_value = fake_client
            content = await fetch_with_jina_reader("https://example.com")

        self.assertEqual(content, "final")
        self.assertEqual(fake_client.get.await_count, 2)
        _, second_kwargs = fake_client.get.await_args_list[1]
        self.assertEqual(second_kwargs["headers"]["Authorization"], "Bearer test-key")

    async def test_raises_when_429_and_no_key(self) -> None:
        from kindly_web_search_mcp_server.content.jina_reader import (
            JinaReaderError,
            fetch_with_jina_reader,
        )

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=_FakeResponse(429, "rate limited"))

        with patch("httpx.AsyncClient") as mock_client_cls, patch.dict(
            os.environ, {"JINA_API_KEY": ""}, clear=False
        ):
            mock_client_cls.return_value.__aenter__.return_value = fake_client
            with self.assertRaises(JinaReaderError):
                await fetch_with_jina_reader("https://example.com")


if __name__ == "__main__":
    unittest.main()
