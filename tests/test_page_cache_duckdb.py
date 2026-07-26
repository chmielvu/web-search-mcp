"""Tests for DuckDB-backed page cache (Phase 5.2).

Written first as failing test per joint plan.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestPageCacheDuckDB(unittest.TestCase):
    def test_url_hash_lookup_and_roundtrip(self) -> None:
        from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "page_cache.sqlite")
            cache = PageSQLiteCache(db_path=db_path)

            url = "https://example.com/doc"
            content = "# Hello\n\nMarkdown content here."
            method = "http_extract"
            meta = {"title": "Example", "links": ["https://ex.com/a"]}

            cache.store(
                canonical_url=url,
                page_content=content,
                extraction_method=method,
                metadata=meta,
            )

            hit = cache.lookup(url)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["page_content"], content)
            self.assertEqual(hit["extraction_method"], method)
            self.assertEqual(hit.get("metadata"), meta)
            self.assertIn("age_seconds", hit)
            self.assertGreaterEqual(hit["word_count"], 4)

            # Different URL (different hash) is miss
            self.assertIsNone(cache.lookup("https://other.com/"))

    def test_ttl_expiry(self) -> None:
        from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "page_cache.sqlite")
            cache = PageSQLiteCache(db_path=db_path)

            url = "https://example.com/ttl-test"
            cache.store(
                canonical_url=url,
                page_content="short lived",
                extraction_method="test",
                ttl_seconds=1,
            )

            self.assertIsNotNone(cache.lookup(url))
            time.sleep(1.1)
            self.assertIsNone(cache.lookup(url))

    def test_metadata_json_roundtrip(self) -> None:
        from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "page_cache.sqlite")
            cache = PageSQLiteCache(db_path=db_path)

            url = "https://example.com/meta"
            meta = {
                "metadata": {"source": "trafilatura", "lang": "en"},
                "links": [{"href": "/a", "text": "A"}],
                "nested": {"deep": [1, 2, {"x": True}]},
            }
            cache.store(
                canonical_url=url,
                page_content="content",
                extraction_method="camoufox_remote",
                metadata=meta,
            )

            hit = cache.lookup(url)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.get("metadata"), meta)

    def test_locked_writes(self) -> None:
        from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "page_cache.sqlite")
            cache = PageSQLiteCache(db_path=db_path)

            errors: list[Exception] = []
            urls = [f"https://example.com/locked/{i}" for i in range(20)]

            def writer(url: str) -> None:
                try:
                    cache.store(
                        canonical_url=url,
                        page_content=f"content for {url}",
                        extraction_method="test",
                        metadata={"i": url[-1]},
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(u,)) for u in urls]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Concurrent write errors: {errors}")

            # All should be readable
            for u in urls:
                hit = cache.lookup(u)
                self.assertIsNotNone(hit, f"Missing after concurrent write: {u}")
                self.assertIn("content for", hit["page_content"])

    def test_uses_separate_duckdb_not_analytics(self) -> None:
        from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            page_path = str(Path(tmp) / "page_cache.sqlite")
            analytics_path = str(Path(tmp) / "analytics.duckdb")

            # Force separate paths
            cache = PageSQLiteCache(db_path=page_path)
            cache.store("https://sep.test/url", "sep content", "test")

            # Analytics path should not have the table or row (we don't create analytics schema here)
            import duckdb

            if Path(analytics_path).exists():
                con = duckdb.connect(analytics_path, read_only=True)
                try:
                    tables = {
                        r[0]
                        for r in con.execute(
                            "SELECT table_name FROM information_schema.tables"
                        ).fetchall()
                    }
                    self.assertNotIn("page_cache", tables)
                finally:
                    con.close()

            # Page path has the table
            import sqlite3

            con = sqlite3.connect(page_path)
            try:
                tables = {
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("page_cache", tables)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
