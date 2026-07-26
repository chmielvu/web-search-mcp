from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from kindly_web_search_mcp_server.content.artifact import ContentArtifact
from kindly_web_search_mcp_server.tools.content import get_content


@pytest.mark.asyncio
async def test_get_content_returns_single_url_field_with_fetched_url() -> None:
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
    with patch("kindly_web_search_mcp_server.tools.content.get_page_cache") as mock_cache:
        mock_cache_inst = AsyncMock()
        mock_cache_inst.alookup.return_value = None
        mock_cache.return_value = mock_cache_inst
        with patch(
            "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
            new_callable=AsyncMock,
            return_value=mock_artifact,
        ):
            result = await get_content("https://example.com/redirect-source", ctx=mock_ctx)

            assert result["url"] == "https://example.com/actual-destination"
            assert "input_url" not in result
            assert "normalized_url" not in result
            assert "fetched_url" not in result
            assert result["status"] == "success"
            assert result["fetch_backend"] == "test_backend"


@pytest.mark.asyncio
async def test_get_content_returns_single_url_field_fallback_when_fetched_url_none() -> None:
    mock_artifact = ContentArtifact(
        input_url="https://example.com/page",
        normalized_url="https://example.com/page",
        fetched_url=None,
        status="success",
        source_type="html",
        fetch_backend="test_backend",
        content_type="text/markdown",
        markdown="# Content",
    )

    mock_ctx = AsyncMock()
    with patch("kindly_web_search_mcp_server.tools.content.get_page_cache") as mock_cache:
        mock_cache_inst = AsyncMock()
        mock_cache_inst.alookup.return_value = None
        mock_cache.return_value = mock_cache_inst
        with patch(
            "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
            new_callable=AsyncMock,
            return_value=mock_artifact,
        ):
            result = await get_content("https://example.com/page", ctx=mock_ctx)

            assert result["url"] == "https://example.com/page"
            assert "input_url" not in result
            assert "normalized_url" not in result
            assert "fetched_url" not in result


@pytest.mark.asyncio
async def test_get_content_cache_hit_provenance() -> None:
    mock_cached = {
        "canonical_url": "https://example.com/cached-page",
        "page_content": "# Cached Content",
        "extraction_method": "jina_reader",
        "metadata": None,
        "links": [],
    }

    mock_ctx = AsyncMock()
    with patch("kindly_web_search_mcp_server.tools.content.get_page_cache") as mock_get_cache:
        mock_cache_inst = AsyncMock()
        mock_cache_inst.alookup.return_value = mock_cached
        mock_get_cache.return_value = mock_get_cache.return_value = mock_cache_inst

        result = await get_content("https://example.com/cached-page", ctx=mock_ctx)

        assert result["url"] == "https://example.com/cached-page"
        assert result["fetch_backend"] == "cache"
        assert result["cached"] is True
        assert result["origin_backend"] == "jina_reader"
