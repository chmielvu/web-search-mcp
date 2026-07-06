"""Tests for YouTube transcript and search functionality."""

from __future__ import annotations

import unittest

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from kindly_web_search_mcp_server.youtube import (
    YouTubeError,
    parse_youtube_url,
    extract_video_id,
    fetch_transcript_data,
    format_transcript_text,
    format_transcript_timestamped,
    calculate_total_duration,
)
from kindly_web_search_mcp_server.youtube.models import TranscriptBackendError
from kindly_web_search_mcp_server.youtube.search import (
    YouTubeSearchError,
    search_youtube_videos,
)
from kindly_web_search_mcp_server.youtube.cascade import (
    fetch_transcript_cascade,
)
from kindly_web_search_mcp_server.youtube.yt_dlp_backend import (
    _parse_json3,
)


class TestParseYouTubeUrl:
    """Test YouTube URL parsing for various formats."""

    def test_bare_video_id(self) -> None:
        """Parse bare 11-char video ID."""
        result = parse_youtube_url("dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"
        assert result.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_watch_url(self) -> None:
        """Parse standard watch URL."""
        result = parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_short_url(self) -> None:
        """Parse youtu.be short URL."""
        result = parse_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_embed_url(self) -> None:
        """Parse embed URL."""
        result = parse_youtube_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_shorts_url(self) -> None:
        """Parse shorts URL."""
        result = parse_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_live_url(self) -> None:
        """Parse live URL."""
        result = parse_youtube_url("https://www.youtube.com/live/dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_url_with_params(self) -> None:
        """Parse watch URL with additional parameters."""
        result = parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=123s")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_mobile_url(self) -> None:
        """Parse mobile YouTube URL."""
        result = parse_youtube_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.video_id == "dQw4w9WgXcQ"

    def test_invalid_url_not_youtube(self) -> None:
        """Reject non-YouTube URL."""
        with pytest.raises(YouTubeError, match="Not a YouTube URL"):
            parse_youtube_url("https://vimeo.com/123456")

    def test_invalid_url_no_video_id(self) -> None:
        """Reject URL without video ID."""
        with pytest.raises(YouTubeError, match="Could not extract video ID"):
            parse_youtube_url("https://www.youtube.com/watch")

    def test_invalid_short_url(self) -> None:
        """Reject youtu.be URL without video ID."""
        with pytest.raises(YouTubeError, match="missing video ID"):
            parse_youtube_url("https://youtu.be/")


class TestExtractVideoId:
    """Test convenience video ID extraction."""

    def test_from_url(self) -> None:
        """Extract from URL."""
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_from_bare_id(self) -> None:
        """Extract from bare ID."""
        assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


class TestFormatTranscript:
    """Test transcript formatting functions."""

    def test_format_text(self) -> None:
        """Format plain text transcript."""
        segments = [
            {"text": "Hello world", "start": 0.0, "duration": 2.0},
            {"text": "Testing transcript", "start": 2.0, "duration": 1.5},
        ]
        result = format_transcript_text(segments)
        assert result == "Hello world Testing transcript"

    def test_format_timestamped(self) -> None:
        """Format timestamped transcript."""
        segments = [
            {"text": "Hello world", "start": 0.0, "duration": 2.0},
            {"text": "Testing transcript", "start": 65.0, "duration": 1.5},
        ]
        result = format_transcript_timestamped(segments)
        assert "[00:00] Hello world" in result
        assert "[01:05] Testing transcript" in result

    def test_calculate_duration(self) -> None:
        """Calculate total duration."""
        segments = [
            {"text": "Hello", "start": 0.0, "duration": 2.0},
            {"text": "World", "start": 10.0, "duration": 5.0},
        ]
        result = calculate_total_duration(segments)
        assert result == 15.0  # 10.0 + 5.0

    def test_empty_segments(self) -> None:
        """Handle empty segments."""
        assert format_transcript_text([]) == ""
        assert format_transcript_timestamped([]) == ""
        assert calculate_total_duration([]) == 0.0


class TestFetchTranscriptData:
    """Test transcript fetching (mocked)."""

    def test_import_error(self) -> None:
        """Handle missing youtube-transcript-api."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "youtube_transcript_api" or name.startswith("youtube_transcript_api."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(YouTubeError, match="not installed"):
                fetch_transcript_data("dQw4w9WgXcQ")

    def test_transcripts_disabled(self) -> None:
        """Handle transcripts disabled error."""
        from youtube_transcript_api._errors import TranscriptsDisabled

        exc = TranscriptsDisabled("test_video_id")
        assert "transcripts" in str(exc).lower() or "disabled" in str(exc).lower()

        error = YouTubeError("Transcripts are disabled for this video")
        assert "disabled" in str(error).lower()


class TestParseYtdlpJson3:
    """Test yt-dlp json3 subtitle parsing."""

    def test_parse_valid_events(self) -> None:
        """Parse standard json3 event structure."""
        import json

        data = {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 2000,
                    "segs": [{"utf8": "Hello "}, {"utf8": "world"}],
                },
                {
                    "tStartMs": 2000,
                    "dDurationMs": 1500,
                    "segs": [{"utf8": "Testing"}],
                },
            ]
        }
        raw = json.dumps(data)
        result = _parse_json3(raw)
        assert len(result) == 2
        assert result[0]["text"] == "Hello world"
        assert result[0]["start"] == 0.0
        assert result[0]["duration"] == 2.0
        assert result[1]["text"] == "Testing"
        assert result[1]["start"] == 2.0

    def test_parse_empty_events(self) -> None:
        """Handle empty events list."""
        import json

        raw = json.dumps({"events": []})
        assert _parse_json3(raw) == []

    def test_parse_skip_empty_segments(self) -> None:
        """Skip events with only whitespace text."""
        import json

        data = {
            "events": [
                {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": " "}]},
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "Real text"}]},
            ]
        }
        raw = json.dumps(data)
        result = _parse_json3(raw)
        assert len(result) == 1
        assert result[0]["text"] == "Real text"

    def test_parse_invalid_json(self) -> None:
        """Handle invalid JSON gracefully."""
        assert _parse_json3("not json") == []

    def test_parse_none_input(self) -> None:
        """Handle None input."""
        assert _parse_json3(None) == []


class TestTranscriptCascade:
    """Test the cascade transcript orchestrator."""

    def test_invalid_backend_raises(self) -> None:
        """Reject unknown backend names."""
        with pytest.raises(ValueError, match="Unknown backend"):
            # Use sync wrapper since fetch_transcript_cascade is sync
            import asyncio

            asyncio.run(fetch_transcript_cascade("dQw4w9WgXcQ", backend="invalid"))

    def test_ytdlp_backend_only_success(self) -> None:
        """backend='ytdlp' uses only yt-dlp path."""
        mock_segments = [{"text": "Hello", "start": 0.0, "duration": 1.0}]
        with patch(
            "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
            return_value=mock_segments,
        ) as mock_ytdlp:
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="ytdlp")
            assert segments == mock_segments
            assert backend_used == "ytdlp"
            mock_ytdlp.assert_called_once()

    def test_api_backend_only_success(self) -> None:
        """backend='api' uses only legacy path."""
        mock_segments = [{"text": "Hello", "start": 0.0, "duration": 1.0}]
        with patch(
            "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
            return_value=mock_segments,
        ) as mock_api:
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="api")
            assert segments == mock_segments
            assert backend_used == "api"
            mock_api.assert_called_once()

    def test_auto_cascade_falls_to_api(self) -> None:
        """auto mode: yt-dlp fails → falls to legacy api."""
        mock_segments = [{"text": "Fallback", "start": 0.0, "duration": 1.0}]
        with (
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=YouTubeError("blocked"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                return_value=mock_segments,
            ),
        ):
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert segments == mock_segments
            assert backend_used == "api"

    def test_auto_cascade_ytdlp_success(self) -> None:
        """auto mode: yt-dlp succeeds on first try."""
        mock_segments = [{"text": "Direct", "start": 0.0, "duration": 1.0}]
        with patch(
            "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
            return_value=mock_segments,
        ):
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert segments == mock_segments
            assert backend_used == "ytdlp"

    def test_all_backends_fail_raises(self) -> None:
        """All backends fail → TranscriptBackendError."""
        with (
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=YouTubeError("yt-dlp failed"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                side_effect=YouTubeError("api failed"),
            ),
        ):
            with pytest.raises(TranscriptBackendError, match="All transcript backends failed"):
                fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")

    def test_ytdlp_empty_subtitles_falls_to_api(self) -> None:
        """yt-dlp returns empty subtitles → falls to api in auto mode."""
        mock_segments = [{"text": "From API", "start": 0.0, "duration": 1.0}]
        with (
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                return_value=[],  # empty = no subtitles
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                return_value=mock_segments,
            ),
        ):
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert segments == mock_segments
            assert backend_used == "api"

    def test_ytdlp_empty_subtitles_only_raises(self) -> None:
        """backend='ytdlp' with empty subtitles → TranscriptBackendError."""
        with patch(
            "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
            return_value=[],
        ):
            with pytest.raises(TranscriptBackendError, match="All transcript backends failed"):
                fetch_transcript_cascade("dQw4w9WgXcQ", backend="ytdlp")

    def test_ytdlp_import_error_falls_to_api(self) -> None:
        """yt-dlp not installed → falls to api gracefully."""
        mock_segments = [{"text": "Fallback", "start": 0.0, "duration": 1.0}]
        with (
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=YouTubeError("yt-dlp not installed. Install with: pip install yt-dlp"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                return_value=mock_segments,
            ),
        ):
            segments, backend_used = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert backend_used == "api"

    def test_translation_passthrough(self) -> None:
        """translate_to is passed through to legacy api."""
        mock_segments = [{"text": "Hola", "start": 0.0, "duration": 1.0}]
        with (
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=YouTubeError("fail"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                return_value=mock_segments,
            ) as mock_api,
        ):
            fetch_transcript_cascade(
                "dQw4w9WgXcQ", language="es", translate_to="es", backend="auto"
            )
            mock_api.assert_called_once_with("dQw4w9WgXcQ", language="es", translate_to="es")


class TestYouTubeSearch(unittest.IsolatedAsyncioTestCase):
    """Test YouTube search via SearXNG."""

    async def test_empty_query(self) -> None:
        """Handle empty query."""
        results = await search_youtube_videos("", num_results=5)
        assert results == []

    async def test_zero_results(self) -> None:
        """Handle zero results request."""
        results = await search_youtube_videos("test", num_results=0)
        assert results == []

    async def test_missing_searxng_config(self) -> None:
        """Handle missing SEARXNG_BASE_URL."""
        with patch.dict("os.environ", {"SEARXNG_BASE_URL": ""}, clear=True):
            with self.assertRaises(YouTubeSearchError) as context:
                await search_youtube_videos("test query", num_results=5)
        self.assertIn("not configured", str(context.exception))

    async def test_successful_search(self) -> None:
        """Handle successful YouTube search."""
        mock_response_data = {
            "results": [
                {
                    "title": "Test Video Title",
                    "url": "https://www.youtube.com/watch?v=test123",
                    "content": "Video description snippet",
                },
                {
                    "title": "Another Video",
                    "url": "https://www.youtube.com/watch?v=test456",
                    "content": "Another description",
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.dict("os.environ", {"SEARXNG_BASE_URL": "http://localhost:8080"}):
            results = await search_youtube_videos(
                "test query", num_results=5, http_client=mock_client
            )

        assert len(results) == 2
        assert results[0].title == "Test Video Title"
        assert results[0].link == "https://www.youtube.com/watch?v=test123"

    async def test_results_capped(self) -> None:
        """Cap results at requested number."""
        mock_response_data = {
            "results": [
                {
                    "title": f"Video {i}",
                    "url": f"https://youtube.com/watch?v={i}",
                    "content": "desc",
                }
                for i in range(10)
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.dict("os.environ", {"SEARXNG_BASE_URL": "http://localhost:8080"}):
            results = await search_youtube_videos(
                "test query", num_results=3, http_client=mock_client
            )

        assert len(results) == 3
