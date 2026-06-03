from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ManualClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TestExactLRUCache(unittest.TestCase):
    def test_lookup_returns_stored_response_for_same_identity(self) -> None:
        from kindly_web_search_mcp_server.cache.exact_lru import ExactLRUCache

        cache = ExactLRUCache(max_entries=2, default_ttl_seconds=60)
        response = {"results": [{"title": "FastMCP"}]}

        cache.store("fastmcp docs", 5, True, "balanced", "searxng", response)

        self.assertEqual(
            cache.lookup("fastmcp docs", 5, True, "balanced", "searxng"),
            response,
        )
        self.assertIsNone(
            cache.lookup("fastmcp docs", 5, True, "balanced", "jina"),
        )

    def test_store_evicts_least_recently_used_entry_when_full(self) -> None:
        from kindly_web_search_mcp_server.cache.exact_lru import ExactLRUCache

        cache = ExactLRUCache(max_entries=2, default_ttl_seconds=60)
        cache.store("first", 5, False, "balanced", "default", {"value": 1})
        cache.store("second", 5, False, "balanced", "default", {"value": 2})

        self.assertEqual(
            cache.lookup("first", 5, False, "balanced", "default"),
            {"value": 1},
        )
        cache.store("third", 5, False, "balanced", "default", {"value": 3})

        self.assertIsNone(cache.lookup("second", 5, False, "balanced", "default"))
        self.assertEqual(
            cache.lookup("first", 5, False, "balanced", "default"),
            {"value": 1},
        )
        self.assertEqual(
            cache.lookup("third", 5, False, "balanced", "default"),
            {"value": 3},
        )

    def test_lookup_expires_entries_after_ttl(self) -> None:
        from kindly_web_search_mcp_server.cache.exact_lru import ExactLRUCache

        clock = ManualClock()
        cache = ExactLRUCache(
            max_entries=2,
            default_ttl_seconds=60,
            time_fn=clock.now,
        )

        cache.store("fastmcp docs", 5, False, "balanced", "default", {"ok": True})
        clock.advance(61)

        self.assertIsNone(cache.lookup("fastmcp docs", 5, False, "balanced", "default"))

    def test_exact_query_cache_preserves_server_lookup_store_signature(self) -> None:
        from kindly_web_search_mcp_server.cache.query_cache import ExactQueryCache

        cache = ExactQueryCache(db_path="ignored", max_entries=2, default_ttl_seconds=60)
        response = {"results": [{"link": "https://example.com"}]}

        cache.store(
            "fastmcp docs",
            5,
            True,
            response,
            search_mode="quality",
            providers_key="searxng",
        )

        self.assertEqual(
            cache.lookup(
                "fastmcp docs",
                5,
                True,
                search_mode="quality",
                providers_key="searxng",
            ),
            response,
        )


if __name__ == "__main__":
    unittest.main()
