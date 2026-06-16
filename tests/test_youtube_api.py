"""Tests for YouTube Data API v3 search provider and quota tracker."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindly_web_search_mcp_server.youtube.api_quota import (
    YouTubeApiQuotaTracker,
)
from kindly_web_search_mcp_server.youtube.api_search import search_youtube_api
from kindly_web_search_mcp_server.youtube.models import YouTubeApiError
from kindly_web_search_mcp_server.youtube.search import search_youtube


# ---------------------------------------------------------------------------
# Quota tracker tests
# ---------------------------------------------------------------------------

class TestYouTubeApiQuotaTracker:
    """Test daily quota tracking with rollover."""

    def test_initial_state(self) -> None:
        """Tracker starts empty."""
        tracker = YouTubeApiQuotaTracker(daily_quota=10000)
        snap = tracker.snapshot()
        assert snap["daily_quota"] == 10000
        assert snap["used"] == 0
        assert snap["remaining"] == 10000
        assert snap["usage_pct"] == 0
        assert snap["call_count"] == 0

    def test_record_call_tracks_units(self) -> None:
        """Calls accumulate units."""
        tracker = YouTubeApiQuotaTracker(daily_quota=10000)
        tracker.record_call(success=True, units=100)
        snap = tracker.snapshot()
        assert snap["used"] == 100
        assert snap["remaining"] == 9900
        assert snap["call_count"] == 1

    def test_record_call_tracks_failures(self) -> None:
        """Failed calls increment failure counter."""
        tracker = YouTubeApiQuotaTracker(daily_quota=10000)
        tracker.record_call(success=True, units=100)
        tracker.record_call(success=False, units=100)
        snap = tracker.snapshot()
        assert snap["used"] == 200
        assert snap["failures"] == 1
        assert snap["call_count"] == 2

    def test_can_afford_within_quota(self) -> None:
        """Returns True when enough quota remains."""
        tracker = YouTubeApiQuotaTracker(daily_quota=10000)
        assert tracker.can_afford(100) is True

    def test_can_afford_exceeds_quota(self) -> None:
        """Returns False when quota would be exceeded."""
        tracker = YouTubeApiQuotaTracker(daily_quota=200)
        tracker.record_call(success=True, units=100)
        tracker.record_call(success=True, units=100)
        assert tracker.can_afford(1) is False

    def test_usage_percentage(self) -> None:
        """Usage percentage is calculated correctly."""
        tracker = YouTubeApiQuotaTracker(daily_quota=10000)
        tracker.record_call(success=True, units=8000)
        snap = tracker.snapshot()
        assert snap["usage_pct"] == 80.0


# ---------------------------------------------------------------------------
# YouTube API search tests
# ---------------------------------------------------------------------------

class TestYouTubeApiSearch(unittest.IsolatedAsyncioTestCase):
    """Test YouTube Data API v3 search."""

    async def test_empty_query_returns_empty(self) -> None:
        """Empty query returns no results."""
        results = await search_youtube_api("", num_results=5)
        assert results == []

    async def test_missing_key_raises(self) -> None:
        """Missing GOOGLE_API_KEY raises YouTubeApiError."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": ""}, clear=False):
            # Force settings reload
            with patch(
                "kindly_web_search_mcp_server.youtube.api_search.settings"
            ) as mock_settings:
                mock_settings.youtube_api_key = ""
                with pytest.raises(YouTubeApiError, match="not configured"):
                    await search_youtube_api("test query", num_results=5)

    async def test_successful_search(self) -> None:
        """Successful API search returns normalized results."""
        mock_response_data = {
            "items": [
                {
                    "id": {"videoId": "abc123"},
                    "snippet": {
                        "title": "Test Video",
                        "description": "A test video description",
                        "channelTitle": "Test Channel",
                    },
                },
                {
                    "id": {"videoId": "def456"},
                    "snippet": {
                        "title": "Another Video",
                        "description": "Another description",
                        "channelTitle": "Other Channel",
                    },
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "kindly_web_search_mcp_server.youtube.api_search.settings"
        ) as mock_settings:
            mock_settings.youtube_api_key = "test-key"
            mock_settings.youtube_api_timeout_seconds = 15.0
            mock_settings.youtube_api_language = ""
            mock_settings.youtube_api_region = ""

            results = await search_youtube_api(
                "test query", num_results=5, http_client=mock_client
            )

        assert len(results) == 2
        assert results[0].title == "Test Video"
        assert results[0].link == "https://www.youtube.com/watch?v=abc123"
        assert "[Test Channel]" in results[0].snippet

    async def test_http_403_raises(self) -> None:
        """HTTP 403 from API raises YouTubeApiError."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": {
                "errors": [{"reason": "quotaExceeded"}]
            }
        }

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "kindly_web_search_mcp_server.youtube.api_search.settings"
        ) as mock_settings:
            mock_settings.youtube_api_key = "test-key"
            mock_settings.youtube_api_timeout_seconds = 15.0
            mock_settings.youtube_api_language = ""
            mock_settings.youtube_api_region = ""

            with pytest.raises(YouTubeApiError, match="403"):
                await search_youtube_api(
                    "test query", num_results=5, http_client=mock_client
                )


class TestYouTubeSearchRouter(unittest.IsolatedAsyncioTestCase):
    """Test the search router logic."""

    async def test_router_prefers_api_when_key_set(self) -> None:
        """Router uses YouTube API when GOOGLE_API_KEY is set."""
        mock_results = [
            MagicMock(title="Video 1", link="https://youtube.com/watch?v=abc"),
        ]

        with patch(
            "kindly_web_search_mcp_server.youtube.search.settings"
        ) as mock_settings, patch(
            "kindly_web_search_mcp_server.youtube.api_search.search_youtube_api",
            new_callable=AsyncMock,
            return_value=mock_results,
        ) as mock_api:
            mock_settings.youtube_api_key = "test-key"

            results, backend = await search_youtube("test query", num_results=5)
            assert backend == "api"
            mock_api.assert_called_once()

    async def test_router_falls_back_to_searxng_without_key(self) -> None:
        """Router uses SearXNG when no API key is set."""
        mock_results = [
            MagicMock(title="Video 1", link="https://youtube.com/watch?v=abc"),
        ]

        with patch(
            "kindly_web_search_mcp_server.youtube.search.settings"
        ) as mock_settings, patch(
            "kindly_web_search_mcp_server.youtube.search.search_youtube_videos",
            new_callable=AsyncMock,
            return_value=mock_results,
        ) as mock_searxng:
            mock_settings.youtube_api_key = ""

            results, backend = await search_youtube("test query", num_results=5)
            assert backend == "searxng"
            mock_searxng.assert_called_once()

    async def test_router_falls_back_to_searxng_on_api_error(self) -> None:
        """Router falls back to SearXNG when API fails."""
        mock_results = [
            MagicMock(title="Fallback Video", link="https://youtube.com/watch?v=xyz"),
        ]

        with patch(
            "kindly_web_search_mcp_server.youtube.search.settings"
        ) as mock_settings, patch(
            "kindly_web_search_mcp_server.youtube.api_search.search_youtube_api",
            new_callable=AsyncMock,
            side_effect=YouTubeApiError("quota exhausted"),
        ), patch(
            "kindly_web_search_mcp_server.youtube.search.search_youtube_videos",
            new_callable=AsyncMock,
            return_value=mock_results,
        ) as mock_searxng:
            mock_settings.youtube_api_key = "test-key"

            results, backend = await search_youtube("test query", num_results=5)
            assert backend == "searxng"
            mock_searxng.assert_called_once()

