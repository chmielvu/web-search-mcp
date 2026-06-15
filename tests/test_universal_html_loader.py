from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeStreamReader(asyncio.StreamReader):
    """A stream reader pre-loaded with data for testing."""

    def __init__(self, data: bytes = b"") -> None:
        super().__init__()
        if data:
            self.feed_data(data)
        self.feed_eof()


class _FakeProc:
    """Minimal async subprocess mock with pipe-based stdout/stderr."""

    def __init__(
        self, html: bytes = b"<html><p>ok</p></html>", stderr: bytes = b""
    ) -> None:
        self.returncode = 0
        self.pid = 12345
        self.stdout = _FakeStreamReader(html)
        self.stderr = _FakeStreamReader(stderr)

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self):
        return b"", b""


class TestUniversalHtmlLoader(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_url_returns_none(self) -> None:
        from kindly_web_search_mcp_server.scrape.universal_html import (
            load_url_as_markdown,
        )

        out = await load_url_as_markdown("https://example.com/file.pdf")
        self.assertIsNone(out)

    async def test_default_total_timeout_is_60(self) -> None:
        from kindly_web_search_mcp_server.scrape.universal_html import (
            UniversalHtmlLoaderConfig,
        )

        config = UniversalHtmlLoaderConfig()
        self.assertEqual(config.total_timeout_seconds, 60.0)

    async def test_converts_html_to_markdown(self) -> None:
        from kindly_web_search_mcp_server.scrape.universal_html import (
            load_url_as_markdown,
        )

        html = "<html><body><main><h1>Title</h1><p>Hello world</p></main></body></html>"

        with patch(
            "kindly_web_search_mcp_server.scrape.universal_html.fetch_html_via_browser",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = html
            out = await load_url_as_markdown("https://example.com")

        self.assertIsInstance(out, str)
        self.assertIn("Title", out)
        self.assertIn("Hello world", out)

    async def test_fetch_html_spawns_crawl4ai_worker(self) -> None:
        from kindly_web_search_mcp_server.scrape.universal_html import (
            fetch_html_via_browser,
        )

        html_bytes = b"<html><body><p>ok</p></body></html>"

        with patch(
            "kindly_web_search_mcp_server.scrape.universal_html.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_spawn:
            mock_spawn.return_value = _FakeProc(html=html_bytes)
            html = await fetch_html_via_browser("https://example.com")

        self.assertIn("ok", html)
        self.assertTrue(mock_spawn.called)
        args, kwargs = mock_spawn.call_args
        self.assertIn("-m", args)
        self.assertIn("kindly_web_search_mcp_server.scrape.crawl4ai_worker", args)
        self.assertIn("env", kwargs)
        self.assertIn("PYTHONPATH", kwargs["env"])

    async def test_fetch_html_backward_compat_alias(self) -> None:
        """fetch_html_via_nodriver is an alias for fetch_html_via_browser."""
        from kindly_web_search_mcp_server.scrape.universal_html import (
            fetch_html_via_browser,
            fetch_html_via_nodriver,
        )

        self.assertIs(fetch_html_via_nodriver, fetch_html_via_browser)

    async def test_fetch_html_sets_no_proxy_for_loopback(self) -> None:
        from kindly_web_search_mcp_server.scrape.universal_html import (
            fetch_html_via_browser,
        )

        html_bytes = b"<html><body><p>ok</p></body></html>"

        with (
            patch.dict(
                "os.environ",
                {"HTTP_PROXY": "http://proxy.invalid:8080"},
                clear=False,
            ),
            patch(
                "kindly_web_search_mcp_server.scrape.universal_html.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_spawn,
        ):
            mock_spawn.return_value = _FakeProc(html=html_bytes)
            await fetch_html_via_browser("https://example.com")

        _args, kwargs = mock_spawn.call_args
        env = kwargs.get("env") or {}
        no_proxy = (env.get("NO_PROXY") or env.get("no_proxy") or "").lower()
        self.assertIn("localhost", no_proxy)
        self.assertIn("127.0.0.1", no_proxy)

    async def test_fetch_html_returns_error_on_nonzero_exit(self) -> None:
        """Verify RuntimeError raised when subprocess exits non-zero."""
        from kindly_web_search_mcp_server.scrape.universal_html import (
            fetch_html_via_browser,
        )

        proc = _FakeProc(html=b"", stderr=b"SomeError: something broke")
        proc.returncode = 1

        with patch(
            "kindly_web_search_mcp_server.scrape.universal_html.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_spawn:
            mock_spawn.return_value = proc
            with self.assertRaises(RuntimeError) as ctx:
                await fetch_html_via_browser("https://example.com")

        self.assertIn("Crawl4AI worker failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
