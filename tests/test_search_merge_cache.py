"""Regression tests for `_memoize_canonicalize` cache sharing.

Covers the dedup of repeated raw URL canonicalization across
`reciprocal_rank_fusion` and `merge_search_results`, with the explicit
default-None path and the supplied-callable path both exercised.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import kindly_web_search_mcp_server.search.merge as merge_mod
from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.merge import (
    _memoize_canonicalize,
    merge_search_results,
    reciprocal_rank_fusion,
)
from kindly_web_search_mcp_server.search.normalize import canonicalize_url


def _r(link: str, providers: list[str] | None = None) -> WebSearchResult:
    return WebSearchResult(
        title="t",
        link=link,
        snippet="s",
        providers=providers or ["p"],
    )


class TestCanonicalizeCaching(unittest.TestCase):
    """Three regression tests for the memoization contract."""

    def test_default_none_path_memoizes_repeated_raw_url(self) -> None:
        """`canonicalize=None` must internally memoize canonicalize_url.

        Patches `merge.canonicalize_url` (the import RRF uses) with a
        counting spy and calls RRF without passing `canonicalize=`, so
        the cache lives inside the RRF call. Repeated raw URLs across
        and within provider lists must trigger the underlying
        canonicalizer once per distinct URL.
        """
        original = merge_mod.canonicalize_url
        call_count = {"n": 0}
        per_raw: dict[str, int] = {}

        def counting(raw: str) -> str:
            call_count["n"] += 1
            per_raw[raw] = per_raw.get(raw, 0) + 1
            return original(raw)

        merge_mod.canonicalize_url = counting
        try:
            # a.com once; b.com in both lists (twice in list_b — second
            # copy is dropped by seen_in_list).
            list_a = [_r("https://a.com/"), _r("https://b.com/")]
            list_b = [_r("https://b.com/"), _r("https://b.com/")]
            fused = reciprocal_rank_fusion([list_a, list_b], k=60)
        finally:
            merge_mod.canonicalize_url = original

        inputs = list_a + list_b
        # Each distinct raw URL was canonicalized exactly once.
        self.assertEqual(per_raw, {r.link: 1 for r in inputs})
        # And the total call count equals the number of distinct URLs.
        self.assertEqual(call_count["n"], len(set(r.link for r in inputs)))
        # RRF semantic contract: 2 distinct buckets, b.com wins.
        self.assertEqual(len(fused), 2)
        self.assertEqual(fused[0][0].link, "https://b.com/")
        # b.com at rank 2 in list_a + rank 1 in list_b (second copy dropped).
        self.assertAlmostEqual(fused[0][1], 1 / 61 + 1 / 62)

    def test_supplied_callable_path_memoizes_repeated_raw_url(self) -> None:
        """A supplied memoized callable is used as-is (not re-wrapped).

        Variants carrying utm_* params collapse to the same canonical
        key (`https://x.com/p`); the counter is the supplied callable
        and must observe exactly the distinct-raw-URL count.
        """
        call_count = {"n": 0}
        per_raw: dict[str, int] = {}

        def counting(raw: str) -> str:
            call_count["n"] += 1
            per_raw[raw] = per_raw.get(raw, 0) + 1
            return canonicalize_url(raw)

        memoized = _memoize_canonicalize(counting)
        list_a = [
            _r("https://x.com/p?utm_source=a"),
            _r("https://x.com/p?utm_source=b"),
        ]
        list_b = [
            _r("https://x.com/p?utm_source=a"),
            _r("https://x.com/p"),
        ]
        inputs = list_a + list_b
        fused = reciprocal_rank_fusion([list_a, list_b], k=60, canonicalize=memoized)

        # Each distinct raw URL was canonicalized exactly once.
        self.assertEqual(per_raw, {r.link: 1 for r in inputs})
        # And the total call count equals the number of distinct URLs.
        self.assertEqual(call_count["n"], len(set(r.link for r in inputs)))
        # utm variants + no-params all collapse to the same canonical key.
        self.assertEqual(len(fused), 1)
        # list_a: utm_source=a at rank 1 contributes, utm_source=b at rank
        # 2 drops (same canonical key already in seen_in_list).
        # list_b: utm_source=a at rank 1 contributes (matches canonical
        # key from list_a), no-params at rank 2 drops.
        self.assertAlmostEqual(fused[0][1], 1 / 61 + 1 / 61)

    def test_merge_search_results_canonicalizes_each_distinct_url_once(self) -> None:
        """`merge_search_results` shares one cache across counter + RRF.

        Without the shared cache, the overlap counter and RRF would each
        canonicalize the same raw URL — i.e. 2× the work for every URL
        appearing in multiple provider lists. With the shared cache, each
        distinct raw URL is canonicalized exactly once.
        """
        original = merge_mod.canonicalize_url
        call_count = {"n": 0}
        per_raw: dict[str, int] = {}

        def counting(raw: str) -> str:
            call_count["n"] += 1
            per_raw[raw] = per_raw.get(raw, 0) + 1
            return original(raw)

        merge_mod.canonicalize_url = counting
        try:
            list_a = [_r("https://a.com/"), _r("https://b.com/")]
            list_b = [_r("https://b.com/")]
            inputs = list_a + list_b
            merged = merge_search_results(
                [list_a, list_b],
                k=60,
                enable_telemetry=False,
            )
        finally:
            merge_mod.canonicalize_url = original

        # Each distinct raw URL was canonicalized exactly once.
        self.assertEqual(per_raw, {r.link: 1 for r in inputs})
        # And the total call count equals the number of distinct URLs.
        self.assertEqual(call_count["n"], len(set(r.link for r in inputs)))
        # b.com merges across lists; rank-2 in list_a + rank-1 in list_b.
        self.assertEqual(merged[0].link, "https://b.com/")
        self.assertAlmostEqual(merged[0].score, 1 / 61 + 1 / 62)


if __name__ == "__main__":
    unittest.main()
