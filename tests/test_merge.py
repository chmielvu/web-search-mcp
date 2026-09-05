from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult


class TestMergeHostCap(unittest.TestCase):
    def _r(self, host: str, idx: int, provider: str = "searxng") -> WebSearchResult:
        return WebSearchResult(
            title=f"{host}-{idx}",
            link=f"https://{host}/p/{idx}",
            snippet=f"snippet-{idx}",
            providers=[provider],
        )

    def test_reciprocal_rank_fusion_contract(self) -> None:
        from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion

        list_a = [self._r("a.com", 1), self._r("b.com", 2)]
        list_b = [self._r("b.com", 2)]

        fused = reciprocal_rank_fusion([list_a, list_b], k=60)
        self.assertEqual(len(fused), 2)
        self.assertEqual(fused[0][0].link.split("/")[2], "b.com")
        self.assertAlmostEqual(fused[0][1], 1 / 61 + 1 / 62)
        self.assertAlmostEqual(fused[1][1], 1 / 61)

    def test_reciprocal_rank_fusion_weighted_lists_scale_contribution(self) -> None:
        from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion

        list_a = [self._r("a.com", 1)]
        list_b = [self._r("b.com", 1)]

        # Equal rank-1 contributions with equal weights tie; encounter
        # order (list_a first) breaks the tie.
        fused_equal = reciprocal_rank_fusion([list_a, list_b], k=60, weights=[1.0, 1.0])
        self.assertEqual(fused_equal[0][0].link.split("/")[2], "a.com")

        # Down-weighting list_a's sole contribution below list_b's flips
        # the winner purely from the weight, not from rank or list count.
        fused_weighted = reciprocal_rank_fusion([list_a, list_b], k=60, weights=[0.5, 1.0])
        self.assertEqual(fused_weighted[0][0].link.split("/")[2], "b.com")
        self.assertAlmostEqual(fused_weighted[0][1], 1.0 / 61)
        self.assertAlmostEqual(fused_weighted[1][1], 0.5 / 61)

    def test_reciprocal_rank_fusion_rejects_mismatched_weights_length(self) -> None:
        from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion

        list_a = [self._r("a.com", 1)]
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([list_a], k=60, weights=[1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
