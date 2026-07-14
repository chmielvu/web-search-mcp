from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestCacheObservability(unittest.TestCase):
    def test_exact_query_cache_emits_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.query_cache import ExactQueryCache

        cache = ExactQueryCache(db_path="unused")

        with (
            patch(
                "kindly_web_search_mcp_server.cache.query_cache.emit_cache_lookup_event"
            ) as emit_lookup,
            patch(
                "kindly_web_search_mcp_server.cache.query_cache.emit_cache_store_event"
            ) as emit_store,
        ):
            cache.store("fastmcp", 10, False, {"results": [1]})
            cache.lookup("fastmcp", 10, False)

        self.assertEqual(emit_lookup.call_args.args[1], "exact")
        self.assertEqual(emit_lookup.call_args.args[2], "hit")
        self.assertEqual(emit_store.call_args.args[1], "exact")
        self.assertEqual(emit_store.call_args.args[2], "ok")

    def test_page_cache_emits_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.page_cache import PageCache

        backend = MagicMock()
        backend.lookup.return_value = {
            "page_content": "hello world",
            "extraction_method": "http_extract",
            "word_count": 2,
            "age_seconds": 4,
            "cached_at": "2026-06-02T00:00:00+00:00",
            "metadata": {"title": "Example"},
        }

        with (
            patch(
                "kindly_web_search_mcp_server.cache.page_cache._PageDuckDBCache",
                return_value=backend,
            ),
            patch(
                "kindly_web_search_mcp_server.cache.page_cache.emit_cache_lookup_event"
            ) as emit_lookup,
            patch(
                "kindly_web_search_mcp_server.cache.page_cache.emit_cache_store_event"
            ) as emit_store,
            patch("kindly_web_search_mcp_server.cache.page_cache.record_cache_lookup"),
        ):
            cache = PageCache(db_path="unused")
            cache.lookup("https://example.com/page")
            cache.store(
                "https://example.com/page",
                "hello world",
                "http_extract",
                metadata={"title": "Example"},
            )

        self.assertEqual(backend.lookup.call_args.args[0], "https://example.com/page")
        self.assertEqual(
            backend.store.call_args.kwargs["canonical_url"],
            "https://example.com/page",
        )
        self.assertEqual(
            backend.store.call_args.kwargs["metadata"],
            {"title": "Example"},
        )
        self.assertEqual(emit_lookup.call_args.args[1], "page")
        self.assertEqual(emit_lookup.call_args.args[2], "hit")
        self.assertEqual(emit_store.call_args.args[1], "page")
        self.assertEqual(emit_store.call_args.args[2], "ok")
        self.assertEqual(emit_store.call_args.kwargs["metadata_present"], True)


if __name__ == "__main__":
    unittest.main()
