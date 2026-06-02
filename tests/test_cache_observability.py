from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestCacheObservability(unittest.TestCase):
    def test_exact_query_cache_emits_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.query_cache import ExactQueryCache

        cache = ExactQueryCache(db_path="unused")
        table = MagicMock()
        table.search.return_value.where.return_value.limit.return_value.to_list.return_value = [
            {
                "created_at": "2026-06-02T00:00:00+00:00",
                "ttl_seconds": 86400,
                "response_json": json.dumps({"results": [1]}),
            }
        ]
        cache._get_table = MagicMock(return_value=table)  # type: ignore[method-assign]

        with patch(
            "kindly_web_search_mcp_server.cache.query_cache.emit_cache_lookup_event"
        ) as emit_lookup, patch(
            "kindly_web_search_mcp_server.cache.query_cache.emit_cache_store_event"
        ) as emit_store:
            cache.lookup("fastmcp", 10, False)
            cache.store("fastmcp", 10, False, {"results": [1]})

        self.assertEqual(emit_lookup.call_args.args[1], "exact")
        self.assertEqual(emit_lookup.call_args.args[2], "hit")
        self.assertEqual(emit_store.call_args.args[1], "exact")
        self.assertEqual(emit_store.call_args.args[2], "ok")

    def test_page_cache_emits_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.page_cache import PageCache

        cache = PageCache(db_path="unused")
        table = MagicMock()
        table.search.return_value.where.return_value.limit.return_value.to_list.return_value = [
            {
                "created_at": "2026-06-02T00:00:00+00:00",
                "ttl_seconds": 604800,
                "page_content": "hello world",
                "extraction_method": "http_extract",
                "word_count": 2,
            }
        ]
        cache._get_table = MagicMock(return_value=table)  # type: ignore[method-assign]

        with patch(
            "kindly_web_search_mcp_server.cache.page_cache.emit_observability_event"
        ) as emit_event:
            cache.lookup("https://example.com/page")
            cache.store(
                "https://example.com/page",
                "hello world",
                "http_extract",
                metadata={"title": "Example"},
            )

        self.assertEqual(emit_event.call_args_list[0].args[1], "search.cache.lookup")
        self.assertEqual(emit_event.call_args_list[1].args[1], "search.cache.store")
        self.assertEqual(emit_event.call_args_list[0].kwargs["cache_type"], "page")
        self.assertEqual(emit_event.call_args_list[1].kwargs["cache_type"], "page")
        self.assertEqual(emit_event.call_args_list[1].kwargs["metadata_present"], True)

    def test_semantic_cache_emits_lookup_and_store_events(self) -> None:
        from kindly_web_search_mcp_server.cache.content_type import ContentType
        from kindly_web_search_mcp_server.cache.semantic_cache import (
            get_semantic_cache,
            set_semantic_cache,
        )

        class _Store:
            def hybrid_search(self, *_args, **_kwargs):
                return [
                    {
                        "answer_json": json.dumps({"result": "cached"}),
                        "created_at": "2026-06-02T00:00:00+00:00",
                        "content_type": ContentType.GENERAL.value,
                        "_distance": 0.1,
                    }
                ]

            def add_entry(self, **_kwargs):
                return None

        async def _run() -> None:
            with patch(
                "kindly_web_search_mcp_server.cache.semantic_cache.embed_query",
                new=AsyncMock(return_value=[0.1, 0.2, 0.3]),
            ), patch(
                "kindly_web_search_mcp_server.cache.semantic_cache.emit_observability_event"
            ) as emit_event:
                await get_semantic_cache(
                    _Store(),
                    "fastmcp docs",
                    min_score=0.05,
                    use_hybrid=True,
                    provider_key="default",
                )
                await set_semantic_cache(
                    _Store(),
                    "fastmcp docs",
                    {"result": "cached"},
                    content_type=ContentType.GENERAL,
                    provider_key="default",
                )

            self.assertEqual(emit_event.call_args_list[0].args[1], "search.cache.lookup")
            self.assertEqual(emit_event.call_args_list[1].args[1], "search.cache.store")
            self.assertEqual(emit_event.call_args_list[0].kwargs["cache_type"], "semantic")
            self.assertEqual(emit_event.call_args_list[1].kwargs["cache_type"], "semantic")

        asyncio.run(_run())

    def test_provider_health_emits_state_change_events(self) -> None:
        from kindly_web_search_mcp_server.search.provider_health import (
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        with patch(
            "kindly_web_search_mcp_server.search.provider_health.emit_observability_event"
        ) as emit_event:
            tracker.mark_failure("searxng")
            tracker.mark_success("searxng")
            tracker.reset("searxng")

        self.assertEqual(emit_event.call_args_list[0].args[1], "provider.health.cooldown")
        self.assertEqual(emit_event.call_args_list[1].args[1], "provider.health.success")
        self.assertEqual(emit_event.call_args_list[2].args[1], "provider.health.reset")
        self.assertEqual(emit_event.call_args_list[0].kwargs["provider"], "searxng")
        self.assertGreaterEqual(emit_event.call_args_list[0].kwargs["cooldown_seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
