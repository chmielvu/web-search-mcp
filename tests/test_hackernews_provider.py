from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestHackerNewsProvider(unittest.IsolatedAsyncioTestCase):
    async def test_irrelevant_query_skips_hackernews(self) -> None:
        from kindly_web_search_mcp_server.search.providers.hackernews import search_hackernews

        results = await search_hackernews("pizza near me", num_results=5)
        self.assertEqual(results, [])

    async def test_discussion_query_searches_story_and_comments(self) -> None:
        import kindly_web_search_mcp_server.search.providers.hackernews as hackernews

        story_response = MagicMock()
        story_response.raise_for_status.return_value = None
        story_response.json.return_value = {
            "hits": [
                {
                    "title": "Rust 1.80 released",
                    "url": "https://example.com/rust",
                    "author": "alice",
                    "points": 123,
                    "num_comments": 45,
                    "created_at": "2026-06-01T12:00:00Z",
                }
            ]
        }
        comment_response = MagicMock()
        comment_response.raise_for_status.return_value = None
        comment_response.json.return_value = {
            "hits": [
                {
                    "comment_text": "Great release!",
                    "story_title": "Rust 1.80 released",
                    "story_url": "https://news.ycombinator.com/item?id=1",
                    "author": "bob",
                    "created_at": "2026-06-01T13:00:00Z",
                    "objectID": "comment-1",
                }
            ]
        }
        client = SimpleNamespace(get=AsyncMock(side_effect=[story_response, comment_response]))

        async def fake_run_provider(
            _provider_name,
            _query,
            _num_results,
            *,
            request,
            parse_response,
            http_client,
            timeout_seconds,
        ):
            data = await request(client)
            return parse_response(data)

        with patch.object(hackernews, "run_provider", side_effect=fake_run_provider):
            results = await hackernews.search_hackernews(
                "latest rust release discussion", num_results=5
            )

        self.assertEqual(
            [item.title for item in results],
            ["Rust 1.80 released", "Comment on Rust 1.80 released"],
        )
        self.assertIn("123 pts", results[0].snippet)
        self.assertIn("45 comments", results[0].snippet)
        self.assertIn("comment on Rust 1.80 released", results[1].snippet)


if __name__ == "__main__":
    unittest.main()
