from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "kindly_web_search_mcp_server" / "rerank" / "diversity.py"
spec = importlib.util.spec_from_file_location("diversity_module", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
maximal_marginal_relevance_rank = module.maximal_marginal_relevance_rank


class TestDiversityRanking(unittest.TestCase):
    def test_real_mmr_promotes_other_hosts_when_query_relevance_close(self) -> None:
        query = [1.0, 0.0]
        embeddings = [[1.0, 0.0], [0.98, 0.02], [0.92, 0.38]]
        urls = ["https://a.com/1", "https://a.com/2", "https://b.com/1"]

        order = maximal_marginal_relevance_rank(query, embeddings, urls, lambda_param=0.7, max_per_host=1)
        self.assertEqual(order[0], 0)
        self.assertEqual(order[1], 2)

    def test_is_deterministic_on_ties(self) -> None:
        query = [1.0, 0.0]
        embeddings = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        urls = ["https://x.com/a", "https://y.com/b", "https://z.com/c"]

        first = maximal_marginal_relevance_rank(query, embeddings, urls)
        second = maximal_marginal_relevance_rank(query, embeddings, urls)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
