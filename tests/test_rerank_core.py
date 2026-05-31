from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


class TestRerankCore(unittest.IsolatedAsyncioTestCase):
    async def test_low_scores_are_not_dropped_by_abs_threshold(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                score=0.05,
            ),
            WebSearchResult(
                title="B",
                link="https://example.com/b",
                snippet="snippet b",
                score=0.04,
            ),
            WebSearchResult(
                title="C",
                link="https://example.com/c",
                snippet="snippet c",
                score=0.03,
            ),
        ]

        with (
            patch(
                "kindly_web_search_mcp_server.rerank.core.embed_query",
                new_callable=AsyncMock,
            ) as mock_embed_query,
            patch(
                "kindly_web_search_mcp_server.rerank.core.voyage_rerank",
                new_callable=AsyncMock,
            ) as mock_voyage_rerank,
        ):
            mock_embed_query.return_value = None
            mock_voyage_rerank.return_value = [(0, 0.05), (1, 0.04), (2, 0.03)]

            reranked = await rerank_results("example query", candidates, top_k=2)

        self.assertEqual(len(reranked), 2)
        self.assertEqual([item.title for item in reranked], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
