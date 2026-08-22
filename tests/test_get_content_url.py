from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.content.artifact import ContentArtifact
from kindly_web_search_mcp_server.tools.content import fetch


@pytest.mark.asyncio
async def test_fetch_returns_resolved_url() -> None:
    mock_artifact = ContentArtifact(
        input_url="https://example.com/redirect-source",
        normalized_url="https://example.com/redirect-source",
        fetched_url="https://example.com/actual-destination",
        status="success",
        source_type="html",
        fetch_backend="test_backend",
        content_type="text/markdown",
        markdown="# Content",
    )
    mock_ctx = AsyncMock()
    mock_ctx.info = AsyncMock()
    with (
        patch(
            "kindly_web_search_mcp_server.tools.content.check_llms_txt",
            new_callable=AsyncMock,
            return_value=type("Probe", (), {"available": False, "url": None})(),
        ),
        patch("kindly_web_search_mcp_server.tools.content.get_page_cache") as mock_cache,
        patch(
            "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
            new_callable=AsyncMock,
            return_value=mock_artifact,
        ),
    ):
        mock_cache_instance = AsyncMock()
        mock_cache_instance.alookup.return_value = None
        mock_cache.return_value = mock_cache_instance
        result = await fetch(url="https://example.com/redirect-source", ctx=mock_ctx)

    assert result.results[0].url == "https://example.com/actual-destination"
    assert result.results[0].fetch_backend == "test_backend"


@pytest.mark.asyncio
async def test_fetch_cache_hit_preserves_origin_backend() -> None:
    cached = {
        "url_canonical": "https://example.com/cached-page",
        "page_content": "# Cached Content",
        "extraction_method": "jina_reader",
        "word_count": 3,
        "metadata": {
            "__web_fetch__": {
                "schema_version": 1,
                "normalized_url": "https://example.com/cached-page",
                "fetched_url": "https://example.com/cached-page",
                "status": "success",
                "source_type": "html",
                "content_type": "text/markdown",
                "origin_backend": "crawl4ai_remote",
                "metadata": {"title": "Cached"},
                "links": [],
            }
        },
    }
    mock_ctx = AsyncMock()
    mock_ctx.info = AsyncMock()
    with (
        patch(
            "kindly_web_search_mcp_server.tools.content.check_llms_txt",
            new_callable=AsyncMock,
            return_value=type("Probe", (), {"available": False, "url": None})(),
        ),
        patch("kindly_web_search_mcp_server.tools.content.get_page_cache") as mock_cache,
    ):
        mock_cache_instance = AsyncMock()
        mock_cache_instance.alookup.return_value = cached
        mock_cache.return_value = mock_cache_instance
        result = await fetch(url="https://example.com/cached-page", ctx=mock_ctx)

    item = result.results[0]
    assert item.cached is True
    assert item.fetch_backend == "cache"
    assert item.origin_backend == "crawl4ai_remote"
    assert item.metadata == {"title": "Cached"}
