"""Behavior contracts for heuristics.lang_detect and QueryFeatures wiring."""

from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.heuristics.lang_detect import detect_lang
from kindly_web_search_mcp_server.heuristics.query_features import build_query_features


class TestDetectLang(unittest.TestCase):
    def test_known_languages(self) -> None:
        self.assertEqual(detect_lang("how to file for divorce in california"), "en")
        self.assertEqual(detect_lang("najlepsza restauracja w warszawie"), "pl")
        self.assertEqual(detect_lang("mejor restaurante cerca de mi"), "es")
        self.assertEqual(detect_lang("wie funktioniert eine waermepumpe"), "de")
        self.assertEqual(detect_lang("comment reduire les frais bancaires"), "fr")

    def test_empty_and_symbol_input(self) -> None:
        self.assertEqual(detect_lang(""), "")
        self.assertEqual(detect_lang("   "), "")
        self.assertEqual(detect_lang("123 456 !!!"), "")


class TestQueryFeaturesWiring(unittest.TestCase):
    def test_english_multiword_gets_lang_no_variant(self) -> None:
        f = build_query_features("duckdb read only connection example")
        self.assertEqual(f.lang, "en")
        self.assertEqual(f.segmented_variants, ())
        self.assertIn("lang.detected:en", f.notes)
        self.assertNotIn("segmented.glued", f.notes)

    def test_glued_single_token_segments_despite_misdetection(self) -> None:
        # lingua reads the glued token as 'de'; single-token queries attempt
        # segmentation regardless of detected language.
        f = build_query_features("toplawyersinnewyork")
        self.assertEqual(f.segmented_variants, ("top lawyers in new york",))
        self.assertIn("segmented.glued", f.notes)

    def test_polish_multiword_never_segmented(self) -> None:
        f = build_query_features("najlepsza restauracja w warszawie")
        self.assertEqual(f.lang, "pl")
        self.assertEqual(f.segmented_variants, ())
        self.assertNotIn("segmented.glued", f.notes)

    def test_cleaned_field_stays_byte_identical(self) -> None:
        f = build_query_features("find toplawyersinnewyork fast")
        self.assertEqual(f.cleaned, "find toplawyersinnewyork fast")
        self.assertEqual(f.segmented_variants, ("find top lawyers in new york fast",))

    def test_operators_and_identifiers_preserved(self) -> None:
        q = "repo:foo/bar lang:python path:src/toplawyersinnewyork file:go"
        f = build_query_features(q)
        self.assertIn("foo/bar", f.repo_slugs)
        self.assertIn("Python", f.languages)
        self.assertIn("src/toplawyersinnewyork", f.path_hints)
        self.assertIsNone(segmented_only(f))


def segmented_only(features) -> str | None:
    """Variant must never exist when nothing eligible was split."""
    return features.segmented_variants[0] if features.segmented_variants else None


if __name__ == "__main__":
    unittest.main()
