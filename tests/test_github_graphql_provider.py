from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestGitHubGraphQLProvider(unittest.IsolatedAsyncioTestCase):
    def test_narrow_query_preserves_explicit_hints(self) -> None:
        from kindly_web_search_mcp_server.search.providers.github_graphql import _narrow_query

        narrowed = _narrow_query("repo:owner/repo issue 123")
        self.assertIn("repo:owner/repo", narrowed)

    def test_format_result_snippet_includes_metadata(self) -> None:
        from kindly_web_search_mcp_server.search.providers.github_graphql import (
            _format_result_snippet,
        )

        snippet = _format_result_snippet(
            {
                "author": {"login": "alice"},
                "repository": {"nameWithOwner": "owner/repo"},
                "comments": {"totalCount": 12},
                "upvoteCount": 7,
                "createdAt": "2026-06-01T12:34:56Z",
                "updatedAt": "2026-06-02T00:00:00Z",
            },
            "discussion",
        )

        self.assertIn("owner/repo", snippet)
        self.assertIn("alice", snippet)
        self.assertIn("12 comments", snippet)
        self.assertIn("created 2026-06-01", snippet)
        self.assertIn("updated 2026-06-02", snippet)

    async def test_search_graphql_raises_on_graphql_errors(self) -> None:
        from kindly_web_search_mcp_server.search.providers.github_graphql import (
            GitHubGraphQLError,
            _DISCUSSION_QUERY,
            _search_graphql,
        )

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errors": [{"message": "bad query"}]}
        client = SimpleNamespace(post=AsyncMock(return_value=response))

        with self.assertRaises(GitHubGraphQLError):
            await _search_graphql(
                client,
                "repo:owner/repo issue 123",
                5,
                _DISCUSSION_QUERY,
                "token",
                "discussion",
            )


if __name__ == "__main__":
    unittest.main()
