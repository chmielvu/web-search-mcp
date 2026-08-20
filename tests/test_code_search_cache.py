from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kindly_web_search_mcp_server.cache.code_search import (
    CodeSearchCache,
    build_search_cache_key,
    is_immutable_revision,
)
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchRequest


class FakePageCache:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.alookup = AsyncMock(side_effect=self._lookup)
        self.astore = AsyncMock(side_effect=self._store)

    async def _lookup(self, key: str) -> dict | None:
        return self.values.get(key)

    async def _store(self, key: str, content: str, method: str, metadata: dict, ttl_seconds: int) -> None:
        self.values[key] = {
            "page_content": content,
            "extraction_method": method,
            "metadata": metadata,
            "ttl_seconds": ttl_seconds,
        }


class Plan:
    variants = ("retry", "backoff")
    variant_pairs = (("retry", "lexical"), ("backoff", "symbol"))
    qualifiers = (("language", "Python"),)


def test_search_cache_round_trip_and_entry_count() -> None:
    cache = CodeSearchCache(search_ttl_seconds=60, search_max_entries=2)
    key = build_search_cache_key(CodeSearchRequest(query="retry"), Plan())
    payload = {"query": "retry", "outcome": "ok", "results": []}

    assert cache.lookup_search(key) is None
    cache.store_search(key, payload)
    assert cache.lookup_search(key) == payload
    assert cache.search_entry_count() == 1


def test_search_cache_key_changes_request_scope() -> None:
    first = build_search_cache_key(CodeSearchRequest(query="retry", language="Python"), Plan())
    second = build_search_cache_key(CodeSearchRequest(query="retry", language="Rust"), Plan())
    assert first != second


@pytest.mark.asyncio
async def test_hydration_cache_requires_immutable_revision_and_round_trips() -> None:
    page_cache = FakePageCache()
    cache = CodeSearchCache(page_cache=page_cache, hydration_ttl_seconds=3600)

    assert is_immutable_revision("main") is False
    assert await cache.lookup_hydration("owner/repo", "README.md", "main") is None

    revision = "a" * 40
    await cache.store_hydration(
        "owner/repo",
        "README.md",
        revision,
        "# README",
        metadata={"blob_oid": revision},
    )
    cached = await cache.lookup_hydration("owner/repo", "README.md", revision)
    assert cached is not None
    assert cached["text"] == "# README"
    assert cached["metadata"]["blob_oid"] == revision
    page_cache.astore.assert_awaited_once()


@pytest.mark.asyncio
async def test_hydration_cache_failure_isolated() -> None:
    page_cache = FakePageCache()
    page_cache.alookup.side_effect = OSError("disk unavailable")
    cache = CodeSearchCache(page_cache=page_cache)

    assert await cache.lookup_hydration("owner/repo", "README.md", "a" * 40) is None
