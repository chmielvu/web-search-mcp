from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


class TestRerankCore(unittest.IsolatedAsyncioTestCase):
    async def test_short_circuit_rerank_records_bypass_metric(self) -> None:
        from kindly_web_search_mcp_server.rerank.core import rerank_results

        candidates = [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                score=0.5,
            )
        ]

        with patch(
            "kindly_web_search_mcp_server.rerank.core.record_rerank_stage",
        ) as mock_record_stage:
            reranked = await rerank_results(
                "example query",
                candidates,
                top_k=10,
                research_goal="Find authoritative docs for the deployment flow",
                query_type_hint="comparison",
            )

        self.assertEqual(reranked.results, candidates)
        mock_record_stage.assert_called_once_with(
            stage="bypass",
            input_count=1,
            output_count=1,
            duration_seconds=0.0,
        )


if __name__ == "__main__":
    unittest.main()
