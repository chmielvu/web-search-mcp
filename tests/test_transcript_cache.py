"""Tests for SQLite-backed transcript cache."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestTranscriptCacheSQLite(unittest.TestCase):
    """Test TranscriptSQLiteCache low-level backend."""

    def test_store_and_lookup_roundtrip(self) -> None:
        """Store segments, lookup returns them."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "transcript_cache.sqlite")
            cache = TranscriptSQLiteCache(db_path=db_path)

            video_id = "abc12345678"
            segments = [
                {"text": "Never gonna give you up", "start": 0.0, "duration": 3.5},
                {"text": "Never gonna let you down", "start": 3.5, "duration": 3.2},
            ]
            duration = 6.7

            cache.store(video_id, "en", None, segments, duration)

            hit = cache.lookup(video_id, language="en")
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(len(hit["segments"]), 2)
            self.assertEqual(hit["segments"][0]["text"], "Never gonna give you up")
            self.assertEqual(hit["segment_count"], 2)
            self.assertAlmostEqual(hit["duration_seconds"], 6.7)
            self.assertIn("age_seconds", hit)

    def test_miss_on_different_language(self) -> None:
        """Store with lang='en', lookup with lang='es' -> miss."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "transcript_cache.sqlite")
            cache = TranscriptSQLiteCache(db_path=db_path)

            video_id = "abc12345678"
            segments = [{"text": "Hello", "start": 0.0, "duration": 1.0}]

            cache.store(video_id, "en", None, segments, 1.0)

            self.assertIsNotNone(cache.lookup(video_id, language="en"))
            self.assertIsNone(cache.lookup(video_id, language="es"))

    def test_miss_on_different_translate_to(self) -> None:
        """Store with translate_to=None, lookup with translate_to='de' -> miss."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "transcript_cache.sqlite")
            cache = TranscriptSQLiteCache(db_path=db_path)

            video_id = "abc12345678"
            segments = [{"text": "Hola", "start": 0.0, "duration": 2.0}]

            cache.store(video_id, "es", None, segments, 2.0)

            self.assertIsNotNone(cache.lookup(video_id, language="es"))
            self.assertIsNone(cache.lookup(video_id, language="es", translate_to="de"))

    def test_ttl_expiry(self) -> None:
        """Store with 1s TTL, sleep 1.1s, verify miss."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "transcript_cache.sqlite")
            cache = TranscriptSQLiteCache(db_path=db_path)

            video_id = "abc12345678"
            segments = [{"text": "short", "start": 0.0, "duration": 1.0}]

            cache.store(video_id, "en", None, segments, 1.0, ttl_seconds=1)

            self.assertIsNotNone(cache.lookup(video_id, language="en"))
            time.sleep(1.1)
            self.assertIsNone(cache.lookup(video_id, language="en"))

    def test_concurrent_writes(self) -> None:
        """20 threads writing different video_ids, no errors."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "transcript_cache.sqlite")
            cache = TranscriptSQLiteCache(db_path=db_path)

            errors: list[Exception] = []

            def writer(vid: str) -> None:
                try:
                    segments = [{"text": f"text for {vid}", "start": 0.0, "duration": 1.0}]
                    cache.store(vid, "en", None, segments, 1.0)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            video_ids = [f"concurrent_{i}" for i in range(20)]
            threads = [threading.Thread(target=writer, args=(vid,)) for vid in video_ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Concurrent write errors: {errors}")

            for vid in video_ids:
                hit = cache.lookup(vid, language="en")
                self.assertIsNotNone(hit, f"Missing after concurrent write: {vid}")
                assert hit is not None
                self.assertIn(f"text for {vid}", hit["segments"][0]["text"])

    def test_separate_sqlite_file(self) -> None:
        """Transcript cache uses its own db path (not page cache)."""
        from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache

        with tempfile.TemporaryDirectory() as tmp:
            transcript_path = str(Path(tmp) / "transcript_cache.sqlite")
            page_path = str(Path(tmp) / "page_cache.sqlite")

            cache = TranscriptSQLiteCache(db_path=transcript_path)
            segments = [{"text": "test", "start": 0.0, "duration": 1.0}]
            cache.store("vid123", "en", None, segments, 1.0)

            import sqlite3

            con = sqlite3.connect(transcript_path)
            try:
                tables = {
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("transcript_cache", tables)
            finally:
                con.close()

            if Path(page_path).exists():
                con = sqlite3.connect(page_path)
                try:
                    tables = {
                        r[0]
                        for r in con.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    self.assertNotIn("transcript_cache", tables)
                finally:
                    con.close()


if __name__ == "__main__":
    unittest.main()
