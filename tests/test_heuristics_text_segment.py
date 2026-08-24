"""Behavior contracts for heuristics.text_segment (always-on segmentation)."""

from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.heuristics.text_segment import (
    MIN_TOKEN_LEN,
    is_eligible_token,
    segment_query,
)


class TestIsEligibleToken(unittest.TestCase):
    def test_long_lowercase_alpha_is_eligible(self) -> None:
        self.assertTrue(is_eligible_token("toplawyersinnewyork"))

    def test_short_tokens_rejected(self) -> None:
        self.assertFalse(is_eligible_token("duckdb"))

    def test_at_threshold_accepted(self) -> None:
        self.assertEqual(MIN_TOKEN_LEN, 10)
        self.assertTrue(is_eligible_token("a" * 10))
        self.assertFalse(is_eligible_token("a" * 9))

    def test_camel_and_snake_rejected(self) -> None:
        self.assertFalse(is_eligible_token("TopLawyers"))
        self.assertFalse(is_eligible_token("top_lawyers_ny"))

    def test_dotted_and_digit_tokens_rejected(self) -> None:
        self.assertFalse(is_eligible_token("best.lawyers.nyc"))
        self.assertFalse(is_eligible_token("iphone15vsstuff"))
        self.assertFalse(is_eligible_token("booking.com"))

    def test_operator_prefixed_rejected(self) -> None:
        self.assertFalse(is_eligible_token("repo:some/longtoken"))
        self.assertFalse(is_eligible_token("site:example.com"))
        self.assertFalse(is_eligible_token("path:averylongpathsegment"))


class TestSegmentQuery(unittest.TestCase):
    def test_splits_flagship_glued_token(self) -> None:
        self.assertEqual(segment_query("toplawyersinnewyork"), "top lawyers in new york")

    def test_preserves_surrounding_words(self) -> None:
        self.assertEqual(
            segment_query("find toplawyersinnewyork fast"),
            "find top lawyers in new york fast",
        )

    def test_returns_none_when_nothing_to_split(self) -> None:
        self.assertIsNone(segment_query(""))
        self.assertIsNone(segment_query("duckdb read only connection example"))
        self.assertIsNone(segment_query("best pizza in chicago"))

    def test_real_long_word_unchanged(self) -> None:
        # 'connection' is a dictionary word; wordninja returns it whole.
        self.assertIsNone(segment_query("connection pooling guide"))

    def test_identifiers_and_operators_untouched(self) -> None:
        q = "repo:foo/bar lang:python path:src/toplawyersinnewyork file:go"
        self.assertIsNone(segment_query(q))

    def test_mixed_case_never_split(self) -> None:
        self.assertIsNone(segment_query("TopLawyersNewYork reviews"))


if __name__ == "__main__":
    unittest.main()
