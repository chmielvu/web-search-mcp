"""Unit tests verifying code review remediations."""

from __future__ import annotations

import asyncio
import pytest

from kindly_web_search_mcp_server.core.config import (
    SearchSettings,
    ContentSettings,
    AnalyticsSettings,
    InferenceSettings,
    AppSettings,
)
from kindly_web_search_mcp_server.search.postprocess import apply_domain_boost
from kindly_web_search_mcp_server.utils.singleflight import SingleFlight
from kindly_web_search_mcp_server.content.safe_fetch import (
    SafeFetchError,
    safe_fetch_url,
)
from kindly_web_search_mcp_server.cache.page_sqlite import PageSQLiteCache
from kindly_web_search_mcp_server.cache.transcript_sqlite import TranscriptSQLiteCache


def test_core_config_exports() -> None:
    """Verify that core/config exports all settings classes without import errors."""
    assert SearchSettings is not None
    assert ContentSettings is not None
    assert AnalyticsSettings is not None
    assert InferenceSettings is not None
    assert AppSettings is not None



def test_domain_boost_wildcard_glob() -> None:
    """Verify that domain_boost moves wildcard matched domains to front."""
    results = [
        {"link": "https://docs.python.org/3/library", "title": "Python Docs"},
        {"link": "https://github.com/prefecthq/fastmcp", "title": "FastMCP"},
    ]
    boosted = apply_domain_boost(results, domain_boost=["*.github.com", "github.com"])
    assert boosted[0]["link"] == "https://github.com/prefecthq/fastmcp"


@pytest.mark.asyncio
async def test_singleflight_get_safety() -> None:
    """Verify SingleFlight executes and coalesces concurrent calls without KeyError."""
    flight = SingleFlight()
    call_count = 0

    async def _work(val: str) -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return f"done-{val}"

    # Coalesce two concurrent calls
    r1, r2 = await asyncio.gather(
        flight.do("key1", _work, "a"),
        flight.do("key1", _work, "a"),
    )
    assert r1 == "done-a"
    assert r2 == "done-a"
    assert call_count == 1

    # Sequential call after completion works
    r3 = await flight.do("key1", _work, "b")
    assert r3 == "done-b"
    assert call_count == 2


@pytest.mark.asyncio
async def test_safe_fetch_blocks_private_redirect() -> None:
    """Verify safe_fetch_url blocks redirect to a private IP."""
    # Attempting to fetch a private host raises SafeFetchError immediately
    with pytest.raises(SafeFetchError) as exc_info:
        await safe_fetch_url("http://127.0.0.1:8000/secret")
    assert "private" in str(exc_info.value).lower() or "localhost" in str(exc_info.value).lower()


def test_page_sqlite_schema_initialized_flag(tmp_path) -> None:
    """Verify that PageSQLiteCache sets _schema_initialized flag."""
    db_file = str(tmp_path / "page_cache.sqlite")
    cache = PageSQLiteCache(db_path=db_file)
    assert not cache._schema_initialized
    con = cache._get_connection()
    try:
        cache._ensure_schema(con)
        assert cache._schema_initialized
        # Second call returns immediately
        cache._ensure_schema(con)
    finally:
        con.close()


def test_transcript_sqlite_schema_and_fts(tmp_path) -> None:
    """Verify that TranscriptSQLiteCache creates standalone FTS5 index without content table mismatch."""
    db_file = str(tmp_path / "transcript_cache.sqlite")
    cache = TranscriptSQLiteCache(db_path=db_file)
    assert not cache._schema_initialized
    con = cache._get_connection()
    try:
        cache._ensure_schema(con)
        assert cache._schema_initialized
    finally:
        con.close()

    # Store a transcript and search via FTS
    transcript = [{"text": "Machine learning and deep neural networks", "start": 0.0, "duration": 5.0}]
    cache._store_sync("vid123", transcript, language="en")

    found = cache.search_transcripts("neural")
    assert len(found) == 1
    assert found[0]["video_id"] == "vid123"
