from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.content.resolvers.raw_text import (
    fetch_raw_text_markdown,
    get_raw_text_type,
    is_raw_text_url,
)
from kindly_web_search_mcp_server.content.safe_fetch import SafeFetchResult


class TestRawTextResolver(unittest.IsolatedAsyncioTestCase):
    def test_is_raw_text_url(self) -> None:
        self.assertTrue(
            is_raw_text_url("https://raw.githubusercontent.com/owner/repo/main/README.md")
        )
        self.assertTrue(
            is_raw_text_url("https://gist.githubusercontent.com/user/123/raw/snippet.txt")
        )
        self.assertTrue(
            is_raw_text_url("https://github.com/owner/repo/raw/main/docs/guide.md")
        )
        self.assertTrue(
            is_raw_text_url("https://gitlab.com/owner/repo/raw/main/README.rst")
        )
        self.assertTrue(is_raw_text_url("https://example.com/doc.md"))
        self.assertTrue(is_raw_text_url("https://example.com/notes.txt"))
        self.assertTrue(is_raw_text_url("https://example.com/guide.markdown"))
        self.assertTrue(is_raw_text_url("https://example.com/doc.mdown"))
        self.assertTrue(is_raw_text_url("https://example.com/data.text?version=1.0#section"))
        self.assertTrue(is_raw_text_url("https://example.com/config.json"))
        self.assertTrue(is_raw_text_url("https://example.com/schema.yaml"))

        self.assertFalse(is_raw_text_url("https://example.com/index.html"))
        self.assertFalse(is_raw_text_url("https://example.com/api/v1"))
        self.assertFalse(is_raw_text_url("https://example.com/"))
    def test_get_raw_text_type(self) -> None:
        self.assertEqual(
            get_raw_text_type("https://example.com/doc.md"), ("text/markdown", "markdown_file")
        )
        self.assertEqual(
            get_raw_text_type("https://example.com/notes.txt"), ("text/plain", "text_file")
        )
        self.assertEqual(
            get_raw_text_type("https://raw.githubusercontent.com/user/repo/main/LICENSE"),
            ("text/markdown", "raw_text"),
        )

    async def test_fetch_raw_text_markdown(self) -> None:
        body = b"# Hello World\n\nThis is raw markdown content."
        fake_result = SafeFetchResult(
            input_url="https://example.com/README.md",
            fetched_url="https://example.com/README.md",
            content_type="text/markdown; charset=utf-8",
            body=body,
            text=body.decode("utf-8"),
            is_pdf=False,
        )

        with patch(
            "kindly_web_search_mcp_server.content.resolvers.raw_text.safe_fetch_url",
            new=AsyncMock(return_value=fake_result),
        ):
            artifact = await fetch_raw_text_markdown("https://example.com/README.md")

        self.assertEqual(artifact.status, "success")
        self.assertEqual(artifact.fetch_backend, "raw_text_fetch")
        self.assertEqual(artifact.source_type, "markdown_file")
        self.assertIn("# Hello World", artifact.markdown)
        self.assertGreater(artifact.word_count, 0)

    async def test_fetch_content_artifact_tier1_routes_raw_text(self) -> None:
        from kindly_web_search_mcp_server.content.fetch_pipeline import fetch_content_artifact

        body = b"Simple text file content."
        fake_result = SafeFetchResult(
            input_url="https://example.com/notes.txt",
            fetched_url="https://example.com/notes.txt",
            content_type="text/plain",
            body=body,
            text=body.decode("utf-8"),
            is_pdf=False,
        )

        with patch(
            "kindly_web_search_mcp_server.content.resolvers.raw_text.safe_fetch_url",
            new=AsyncMock(return_value=fake_result),
        ):
            artifact = await fetch_content_artifact("https://example.com/notes.txt")

        self.assertEqual(artifact.status, "success")
        self.assertEqual(artifact.fetch_backend, "raw_text_fetch")
        self.assertEqual(artifact.source_type, "text_file")
        self.assertEqual(artifact.markdown, "Simple text file content.")

    async def test_safe_fetch_url_allows_markdown_and_plain_text(self) -> None:
        import httpx
        from kindly_web_search_mcp_server.content.safe_fetch import safe_fetch_url

        def handler(request: httpx.Request) -> httpx.Response:
            if "markdown" in request.url.path:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/markdown; charset=utf-8"},
                    content=b"# Markdown Header\n\nMarkdown body content.",
                )
            elif "plain" in request.url.path:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/plain"},
                    content=b"Plain text body content.",
                )
            elif "json" in request.url.path:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=b'{"key": "value"}',
                )
            elif "raw_octet" in request.url.path:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/octet-stream"},
                    content=b"# Raw Text from GitHub\n\nSome code content.",
                )
            elif "image" in request.url.path:
                return httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        orig_async_client = httpx.AsyncClient

        with patch(
            "kindly_web_search_mcp_server.content.safe_fetch.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: orig_async_client(
                transport=transport,
                follow_redirects=kwargs.get("follow_redirects", True),
                timeout=kwargs.get("timeout"),
            ),
        ):
            res_md = await safe_fetch_url("https://example.com/file.markdown")
            self.assertEqual(res_md.content_type, "text/markdown; charset=utf-8")
            self.assertIn("# Markdown Header", res_md.text)

            res_plain = await safe_fetch_url("https://example.com/file.plain")
            self.assertEqual(res_plain.content_type, "text/plain")
            self.assertIn("Plain text", res_plain.text)

            res_json = await safe_fetch_url("https://example.com/file.json")
            self.assertEqual(res_json.content_type, "application/json")
            self.assertIn('"key": "value"', res_json.text)

            res_octet = await safe_fetch_url("https://raw.githubusercontent.com/user/repo/main/raw_octet.md")
            self.assertIn("Raw Text from GitHub", res_octet.text)

            from kindly_web_search_mcp_server.content.safe_fetch import SafeFetchError
            with self.assertRaises(SafeFetchError) as ctx:
                await safe_fetch_url("https://example.com/file.image")
            self.assertEqual(ctx.exception.code, "unsupported_content_type")
    # NOTE: test_batch_fetch_uses_raw_text_resolver was removed — it tested
    # batch_orchestrator internals which no longer exist. Raw text resolver
    # routing is covered by test_fetch_content_artifact_tier1_routes_raw_text above.


if __name__ == "__main__":
    unittest.main()
