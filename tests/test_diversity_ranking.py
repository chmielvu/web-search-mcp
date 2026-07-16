from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.rerank.diversity import select_diverse_slate


class TestDiversityRanking(unittest.TestCase):
    def test_no_trigger_preserves_first_slate(self) -> None:
        selection = select_diverse_slate(
            [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
            ["https://a.com/1", "https://b.com/1", "https://c.com/1"],
            output_size=2,
            lambda_param=0.8,
            similarity_threshold=0.85,
            max_per_host=2,
        )
        self.assertFalse(selection.triggered)
        self.assertEqual(selection.selected_indices, (0, 1))
        self.assertEqual(selection.remaining_indices, (2,))

    def test_similarity_trigger_uses_rank_prior_and_incoming_tie_break(self) -> None:
        selection = select_diverse_slate(
            [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
            ["https://a.com/1", "https://b.com/1", "https://c.com/1"],
            output_size=2,
            lambda_param=0.7,
            similarity_threshold=0.85,
            max_per_host=2,
        )
        self.assertTrue(selection.triggered)
        self.assertEqual(selection.selected_indices, (0, 2))
        self.assertEqual(selection.remaining_indices, (1,))

    def test_host_rule_prefers_hosts_below_cap_then_backfills(self) -> None:
        selection = select_diverse_slate(
            [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]],
            ["https://a.com/1", "https://a.com/2", "https://b.com/1"],
            output_size=3,
            lambda_param=1.0,
            similarity_threshold=1.0,
            max_per_host=1,
        )
        self.assertTrue(selection.triggered)
        self.assertEqual(selection.selected_indices, (0, 2, 1))

    def test_negative_cosine_is_clamped_to_zero(self) -> None:
        selection = select_diverse_slate(
            [[1.0, 0.0], [-1.0, 0.0]],
            ["https://a.com/1", "https://b.com/1"],
            output_size=1,
            lambda_param=0.8,
            similarity_threshold=0.1,
            max_per_host=2,
        )
        self.assertEqual(selection.max_pairwise_similarity, 0.0)
        self.assertFalse(selection.triggered)

    def test_shape_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "one nonzero dimension"):
            select_diverse_slate(
                [[1.0, 0.0], [1.0]],
                ["https://a.com/1", "https://b.com/1"],
                output_size=1,
                lambda_param=0.8,
                similarity_threshold=0.85,
                max_per_host=2,
            )


if __name__ == "__main__":
    unittest.main()
