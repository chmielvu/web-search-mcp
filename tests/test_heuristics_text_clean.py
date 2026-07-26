"""Behavior contracts for heuristics.text_clean."""

from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.heuristics.text_clean import (
    clean_query,
    clean_text_for_llm,
    repair_unicode,
)
from kindly_web_search_mcp_server.search.normalize import normalize_query


class TestHeuristicsTextClean(unittest.TestCase):
    def test_clean_query_collapses_ws_and_quotes(self) -> None:
        self.assertEqual(clean_query("  foo   bar  "), "foo bar")
        # curly quotes → straight
        self.assertEqual(clean_query("\u201cfoo\u201d \u2018bar\u2019"), "\"foo\" 'bar'")
        # en/em dash → hyphen
        self.assertEqual(clean_query("foo\u2013bar\u2014baz"), "foo-bar-baz")

    def test_repair_unicode_mojibake(self) -> None:
        try:
            import ftfy  # noqa: F401
        except ImportError:
            self.skipTest("ftfy not installed")
        # Classic mojibake for ’
        repaired = repair_unicode("it\u00e2\u20ac\u2122s")
        self.assertIn("'", repaired)

    def test_normalize_query_delegates(self) -> None:
        samples = [
            "  a   b  ",
            "\u201chello\u201d world",
            "repo:foo/bar  path:src",
            "",
        ]
        for sample in samples:
            self.assertEqual(normalize_query(sample), clean_query(sample))

    def test_clean_text_for_llm_roles(self) -> None:
        page = clean_text_for_llm("hello   world\n\n\nnext", role="page")
        self.assertEqual(page, "hello world\n\nnext")
        # zero-width chars are removed; adjacent letters stay adjacent
        snip = clean_text_for_llm("  x\u200by  z  ", role="snippet")
        self.assertEqual(snip, "xy z")
        tr = clean_text_for_llm("a\n\n\n\nb", role="transcript")
        self.assertEqual(tr, "a\n\nb")


if __name__ == "__main__":
    unittest.main()
