"""Tests for YouTube Data API v3 video metadata enrichment."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.youtube.api_enrichment import (
    _parse_iso8601_duration,
    enrich_video_metadata,
    merge_enrichment_into_results,
)


# ---------------------------------------------------------------------------
# ISO 8601 duration parsing
# ---------------------------------------------------------------------------


class TestParseIso8601Duration:
    """Test _parse_iso8601_duration with various inputs."""

    def test_seconds_only(self) -> None:
        assert _parse_iso8601_duration("PT4S") == 4.0

    def test_minutes_and_seconds(self) -> None:
        assert _parse_iso8601_duration("PT4M13S") == 253.0

    def test_hours_minutes_seconds(self) -> None:
        assert _parse_iso8601_duration("PT1H2M3S") == 3723.0

    def test_zero_duration(self) -> None:
        assert _parse_iso8601_duration("PT0S") == 0.0

    def test_hours_only(self) -> None:
        assert _parse_iso8601_duration("PT2H") == 7200.0

    def test_minutes_only(self) -> None:
        assert _parse_iso8601_duration("PT30M") == 1800.0

    def test_empty_string_returns_zero(self) -> None:
        assert _parse_iso8601_duration("") == 0.0

    def test_malformed_string_returns_zero(self) -> None:
        assert _parse_iso8601_duration("bogus") == 0.0


# ---------------------------------------------------------------------------
# enrich_video_metadata
# ---------------------------------------------------------------------------


class TestEnrichVideoMetadata(unittest.IsolatedAsyncioTestCase):
    """Test batch enrichment via videos.list endpoint."""

    async def test_enrich_success(self) -> None:
        """Successful enrichment returns metadata dict keyed by video ID."""
        mock_response_data = {
            "items": [
                {
                    "id": "abc123",
                    "snippet": {
                        "channelTitle": "Test Channel",
                        "publishedAt": "2024-03-15T10:00:00Z",
                        "thumbnails": {
                            "high": {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"},
                        },
                    },
                    "contentDetails": {
                        "duration": "PT4M13S",
                        "caption": "true",
                    },
                    "statistics": {
                        "viewCount": "1234567",
                        "likeCount": "50000",
                    },
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("kindly_web_search_mcp_server.youtube.api_enrichment.settings") as mock_settings:
            mock_settings.youtube_api_key = "test-key"
            mock_settings.youtube_api_timeout_seconds = 15.0

            result = await enrich_video_metadata(
                ["abc123"],
                http_client=mock_client,
            )

        assert "abc123" in result
        meta = result["abc123"]
        assert meta["duration_seconds"] == 253.0
        assert meta["view_count"] == 1234567
        assert meta["like_count"] == 50000
        assert meta["channel_title"] == "Test Channel"
        assert meta["has_captions"] is True
        assert meta["published_date"] == "2024-03-15"

    async def test_enrich_empty_ids(self) -> None:
        """Empty video ID list returns empty dict without making API call."""
        result = await enrich_video_metadata([], http_client=None)
        assert result == {}

    async def test_enrich_missing_key(self) -> None:
        """Missing API key returns empty dict gracefully."""
        with patch("kindly_web_search_mcp_server.youtube.api_enrichment.settings") as mock_settings:
            mock_settings.youtube_api_key = ""
            result = await enrich_video_metadata(["abc123"], http_client=None)
        assert result == {}

    async def test_enrich_http_error(self) -> None:
        """HTTP error returns empty dict (graceful degradation)."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.raise_for_status = MagicMock(side_effect=Exception("Service Unavailable"))

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("kindly_web_search_mcp_server.youtube.api_enrichment.settings") as mock_settings:
            mock_settings.youtube_api_key = "test-key"
            mock_settings.youtube_api_timeout_seconds = 15.0

            result = await enrich_video_metadata(
                ["abc123"],
                http_client=mock_client,
            )

        assert result == {}

    async def test_enrich_http_403_returns_empty(self) -> None:
        """HTTP 403 returns empty dict and records failed quota call."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("kindly_web_search_mcp_server.youtube.api_enrichment.settings") as mock_settings:
            mock_settings.youtube_api_key = "test-key"
            mock_settings.youtube_api_timeout_seconds = 15.0

            result = await enrich_video_metadata(
                ["abc123"],
                http_client=mock_client,
            )

        assert result == {}


# ---------------------------------------------------------------------------
# merge_enrichment_into_results
# ---------------------------------------------------------------------------


class TestMergeEnrichmentIntoResults:
    """Test merging enrichment metadata into search results."""

    def test_merge_appends_metadata_to_snippet(self) -> None:
        """Metadata is appended to snippet field as newline-separated line."""
        results = [
            WebSearchResult(
                title="Test Video",
                link="https://www.youtube.com/watch?v=abc123",
                snippet="Original description text",
            ),
        ]
        metadata = {
            "abc123": {
                "duration_seconds": 253.0,
                "view_count": 100000,
                "like_count": 5000,
                "channel_title": "Test Channel",
                "thumbnail_url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
                "has_captions": True,
                "published_date": "2024-03-15",
            },
        }

        enriched = merge_enrichment_into_results(results, metadata)
        assert len(enriched) == 1
        assert "Original description text" in enriched[0].snippet
        assert "Duration: 4m 13s" in enriched[0].snippet
        assert "100,000 views" in enriched[0].snippet
        assert "5,000 likes" in enriched[0].snippet
        assert "Published: 2024-03-15" in enriched[0].snippet
        assert "Captions available" in enriched[0].snippet
        assert enriched[0].published_date == "2024-03-15"

    def test_merge_empty_metadata_returns_unchanged(self) -> None:
        """Empty metadata dict returns results unchanged."""
        results = [
            WebSearchResult(
                title="Test",
                link="https://www.youtube.com/watch?v=abc123",
                snippet="desc",
            ),
        ]
        enriched = merge_enrichment_into_results(results, {})
        assert enriched[0].snippet == "desc"

    def test_merge_non_youtube_link_unchanged(self) -> None:
        """Results without YouTube watch URLs are returned unchanged."""
        results = [
            WebSearchResult(
                title="Not YouTube",
                link="https://example.com",
                snippet="Some text",
            ),
        ]
        metadata = {"abc123": {"duration_seconds": 100.0}}
        enriched = merge_enrichment_into_results(results, metadata)
        assert enriched[0].snippet == "Some text"

    def test_merge_does_not_overwrite_published_date(self) -> None:
        """Existing published_date is not overwritten by enrichment."""
        results = [
            WebSearchResult(
                title="Test Video",
                link="https://www.youtube.com/watch?v=abc123",
                snippet="desc",
                published_date="2023-01-01",
            ),
        ]
        metadata = {
            "abc123": {
                "duration_seconds": 60.0,
                "view_count": 100,
                "like_count": 10,
                "channel_title": "",
                "thumbnail_url": "",
                "has_captions": False,
                "published_date": "2024-06-01",
            },
        }
        enriched = merge_enrichment_into_results(results, metadata)
        # Published date should not change since result already had one
        assert enriched[0].published_date == "2023-01-01"
