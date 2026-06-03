"""TDD tests for Phase 7.1 Qdrant result memory store.

Run with:
powershell -NoProfile -Command "& { .\.venv\Scripts\python.exe -m pytest tests/test_result_memory.py -q }"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestResultMemoryStore(unittest.TestCase):
    def test_uses_qdrant_local_memory_or_path(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        mem = ResultMemoryStore(path=None)  # forces :memory:
        self.assertIsNotNone(mem.client)
        # path variant also constructs
        mem2 = ResultMemoryStore(path=":memory:")  # explicit also works as memory for test
        self.assertIsNotNone(mem2.client)

    def test_collection_naming_by_embedding_model_and_dim(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        store = ResultMemoryStore(
            path=None, embedding_model="ibm-granite/granite-embedding-97m-multilingual-r2", dim=384
        )
        name = store.collection_name
        self.assertTrue(name.startswith("result_memory_"))
        # full default model "ibm-granite/granite-..." is sanitized with prefix kept
        self.assertIn("granite-embedding-97m-multilingual-r2", name)
        self.assertIn("ibm-granite", name)
        self.assertTrue(name.endswith("_384"))

        store2 = ResultMemoryStore(path=None, embedding_model="test/model:v1", dim=768)
        self.assertIn("test_model_v1", store2.collection_name)
        self.assertTrue(store2.collection_name.endswith("_768"))

    def test_deterministic_point_ids_and_no_duplicate_query_result_stores(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        store = ResultMemoryStore(path=None, embedding_model="test", dim=8)
        emb = [0.01] * 8
        r1 = {"title": "Result A", "link": "https://example.com/a", "snippet": "first"}
        r2 = {"title": "Result A updated", "link": "https://example.com/a", "snippet": "second"}

        store.store_results("same query", emb, [r1])
        store.store_results("same query", emb, [r2])  # same url -> upsert, no dup

        # inspect count via client (test hook)
        count = store.client.count(collection_name=store.collection_name, exact=True).count
        self.assertEqual(count, 1)

        # different url -> separate point
        r3 = {"title": "B", "link": "https://example.com/b", "snippet": "b"}
        store.store_results("same query", emb, [r3])
        count = store.client.count(collection_name=store.collection_name, exact=True).count
        self.assertEqual(count, 2)

    def test_payload_roundtrip_via_lookup(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        store = ResultMemoryStore(path=None, embedding_model="t", dim=4)
        emb = [0.1, 0.2, 0.3, 0.4]
        results = [
            {"title": "Title 1", "link": "https://ex.com/1", "snippet": "snippet one here"},
            {"title": "Title 2", "link": "https://ex.com/2", "snippet": "snippet two"},
        ]
        store.store_results("original query text", emb, results, entities=[{"label": "pkg", "text": "ex"}])

        cands = store.lookup_candidates(emb, limit=2, min_similarity=0.0)
        self.assertEqual(len(cands), 2)
        titles = {c["title"] for c in cands}
        self.assertEqual(titles, {"Title 1", "Title 2"})
        # content roundtrip for both
        urls = {c["url"] for c in cands}
        self.assertEqual(urls, {"https://ex.com/1", "https://ex.com/2"})
        self.assertTrue(all("similarity" in c and "source_query" in c for c in cands))
        self.assertTrue(any(c["source_query"] == "original query text" for c in cands))

    def test_age_decay_reduces_adjusted_score(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore
        from datetime import datetime, timezone, timedelta

        store = ResultMemoryStore(path=None, embedding_model="t", dim=4)
        emb = [0.5] * 4
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        # directly upsert an old point to simulate age (bypass store which sets now)
        from qdrant_client.models import PointStruct
        import uuid as _uuid
        old_id = str(_uuid.uuid4())
        old_point = PointStruct(
            id=old_id,
            vector=emb,
            payload={
                "query_text": "q",
                "result_url": "https://old.com",
                "result_title": "Old",
                "result_snippet": "old",
                "entities_json": "[]",
                "created_at": old_ts,
            },
        )
        store.client.upsert(collection_name=store.collection_name, points=[old_point])

        cands = store.lookup_candidates(emb, limit=1, min_similarity=0.0)
        self.assertEqual(len(cands), 1)
        # adjusted should be lower due to decay; base sim ~1.0 for exact vec match
        self.assertLess(cands[0]["adjusted_score"], 0.8)

    def test_entity_overlap_boost_increases_score_even_without_full_entity_impl(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        store = ResultMemoryStore(path=None, embedding_model="t", dim=4)
        emb = [0.9] * 4
        results = [{"title": "T", "link": "https://e.com", "snippet": "S"}]
        store.store_results("q", emb, results, entities=[{"label": "package", "text": "fastmcp"}])

        # no entities -> base
        cands_no = store.lookup_candidates(emb, limit=1, min_similarity=0.0, query_entities=None)
        base_adj = cands_no[0]["adjusted_score"]

        # matching entity -> boost
        cands_yes = store.lookup_candidates(
            emb, limit=1, min_similarity=0.0, query_entities=[{"label": "package", "text": "fastmcp"}]
        )
        boosted = cands_yes[0]["adjusted_score"]

        self.assertGreater(boosted, base_adj)
        self.assertGreater(cands_yes[0]["entity_overlap"], 0.0)

    def test_emits_result_memory_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore

        store = ResultMemoryStore(path=None, embedding_model="t", dim=4)
        emb = [0.0] * 4

        captured: list[tuple[str, dict]] = []

        def fake_emit(logger, event, **fields):
            captured.append((event, fields))

        with patch(
            "kindly_web_search_mcp_server.cache.result_memory.emit_observability_event", side_effect=fake_emit
        ):
            store.store_results("q", emb, [{"title": "x", "link": "u", "snippet": "s"}])
            _ = store.lookup_candidates(emb, limit=1, min_similarity=0.0)

        events = [e for e, _ in captured]
        self.assertIn("result_memory.store", events)
        self.assertIn("result_memory.lookup", events)

    def test_result_memory_respects_settings_path(self) -> None:
        from kindly_web_search_mcp_server import settings as settings_mod
        from kindly_web_search_mcp_server.cache.result_memory import ResultMemoryStore, get_result_memory_store

        original = settings_mod.settings.result_memory_path
        try:
            settings_mod.settings.result_memory_path = ""
            # get should give in-memory
            s1 = get_result_memory_store()
            self.assertIsNotNone(s1)
            # reset global for test
            # direct construct with path from settings
            settings_mod.settings.result_memory_path = ".kindly/test_result_mem_for_test"
            s2 = ResultMemoryStore(
                path=settings_mod.settings.result_memory_path,
                embedding_model="t",
                dim=4,
            )
            self.assertIsNotNone(s2.client)
        finally:
            settings_mod.settings.result_memory_path = original


if __name__ == "__main__":
    unittest.main()
