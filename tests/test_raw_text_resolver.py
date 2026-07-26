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
        self.assertTrue(is_raw_text_url("https://example.com/doc.md"))
        self.assertTrue(is_raw_text_url("https://example.com/notes.txt"))
        self.assertTrue(is_raw_text_url("https://example.com/guide.markdown"))
        self.assertTrue(is_raw_text_url("https://example.com/data.text?version=1.0#section"))

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

    async def test_batch_fetch_uses_raw_text_resolver(self) -> None:
        from kindly_web_search_mcp_server.content.batch_orchestrator import (
            BatchParams,
            run_batch_fetch,
        )

        body_md = b"# Guide\n\nContent for guide."
        fake_result_md = SafeFetchResult(
            input_url="https://example.com/guide.md",
            fetched_url="https://example.com/guide.md",
            content_type="text/markdown",
            body=body_md,
            text=body_md.decode("utf-8"),
            is_pdf=False,
        )

        body_txt = b"Log line 1\nLog line 2"
        fake_result_txt = SafeFetchResult(
            input_url="https://example.com/log.txt",
            fetched_url="https://example.com/log.txt",
            content_type="text/plain",
            body=body_txt,
            text=body_txt.decode("utf-8"),
            is_pdf=False,
        )

        async def _mock_safe_fetch(url, **kwargs):
            if "guide.md" in url:
                return fake_result_md
            return fake_result_txt

        with patch(
            "kindly_web_search_mcp_server.content.resolvers.raw_text.safe_fetch_url",
            side_effect=_mock_safe_fetch,
        ):
            out = await run_batch_fetch(
                urls=["https://example.com/guide.md", "https://example.com/log.txt"],
                params=BatchParams(
                    max_concurrency=2, per_item_char_length=1000, total_char_budget=5000
                ),
                cursor=None,
            )

        self.assertEqual(out["total_requested"], 2)
        self.assertEqual(out["total_returned"], 2)
        self.assertEqual(out["results"][0]["fetch_backend"], "raw_text_fetch")
        self.assertEqual(out["results"][1]["fetch_backend"], "raw_text_fetch")
        self.assertIn("# Guide", out["results"][0]["page_content"])
        self.assertIn("Log line 1", out["results"][1]["page_content"])


if __name__ == "__main__":
    unittest.main()
