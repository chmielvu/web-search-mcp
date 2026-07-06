"""Tests for Whisper ASR client and cascade integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from kindly_web_search_mcp_server.youtube.whisper_client import (
    WhisperClientError,
    _parse_gradio_response,
    fetch_whisper_transcript_sync,
)


# ---------------------------------------------------------------------------
# Gradio response parsing
# ---------------------------------------------------------------------------


class TestGradioFormatParsing:
    """Verify /api/predict response envelope is parsed correctly."""

    def test_parse_segments_json(self) -> None:
        """Standard Gradio {"data": ["<json>"]} with segments."""
        inner = {
            "segments": [
                {"text": "Hello world", "start": 0.0, "duration": 2.5},
                {"text": "Second line", "start": 2.5, "duration": 1.0},
            ],
            "language": "en",
            "duration_seconds": 3.5,
        }
        raw = {"data": [json.dumps(inner)]}
        segments = _parse_gradio_response(raw)
        assert len(segments) == 2
        assert segments[0]["text"] == "Hello world"
        assert segments[0]["start"] == 0.0
        assert segments[0]["duration"] == 2.5
        assert segments[1]["text"] == "Second line"

    def test_parse_missing_data_raises(self) -> None:
        """Missing data key → WhisperClientError."""
        with pytest.raises(WhisperClientError, match="no 'data'"):
            _parse_gradio_response({})

    def test_parse_empty_data_raises(self) -> None:
        """Empty data array → WhisperClientError."""
        with pytest.raises(WhisperClientError, match="no 'data'"):
            _parse_gradio_response({"data": []})

    def test_parse_non_string_data_raises(self) -> None:
        """Non-string data[0] → WhisperClientError."""
        with pytest.raises(WhisperClientError, match="not a string"):
            _parse_gradio_response({"data": [42]})


class TestPlainTextFallback:
    """Non-JSON responses are converted to a single segment."""

    def test_plain_text_to_single_segment(self) -> None:
        """Plain text transcript → one segment with start=0, duration=0."""
        raw = {"data": ["This is a plain text transcript."]}
        segments = _parse_gradio_response(raw)
        assert len(segments) == 1
        assert segments[0]["text"] == "This is a plain text transcript."
        assert segments[0]["start"] == 0.0
        assert segments[0]["duration"] == 0.0

    def test_json_without_segments_key(self) -> None:
        """Valid JSON but no segments key → plain text fallback."""
        raw = {"data": [json.dumps({"text": "no segments key"})]}
        segments = _parse_gradio_response(raw)
        assert len(segments) == 1
        assert segments[0]["start"] == 0.0

    def test_empty_segments_list_falls_back(self) -> None:
        """Empty segments list → plain text fallback."""
        inner = {"segments": []}
        raw = {"data": [json.dumps(inner)]}
        segments = _parse_gradio_response(raw)
        assert len(segments) == 1


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


class TestFetchSync:
    """Test the synchronous fetch_whisper_transcript_sync function."""

    @patch("kindly_web_search_mcp_server.youtube.whisper_client.settings")
    def test_success(self, mock_settings: MagicMock) -> None:
        """Successful Gradio round-trip returns segments."""
        mock_settings.whisper_space_url = "https://whisper.hf.space"
        mock_settings.whisper_space_timeout_seconds = 300.0

        inner = {
            "segments": [
                {"text": "Hello", "start": 0.0, "duration": 1.0},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [json.dumps(inner)]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            segments = fetch_whisper_transcript_sync(
                "https://www.youtube.com/watch?v=abc12345678",
                timeout_seconds=60.0,
            )

        assert len(segments) == 1
        assert segments[0]["text"] == "Hello"
        mock_client.post.assert_called_once()

    @patch("kindly_web_search_mcp_server.youtube.whisper_client.settings")
    def test_missing_url_raises(self, mock_settings: MagicMock) -> None:
        """No WHISPER_SPACE_URL configured → WhisperClientError."""
        mock_settings.whisper_space_url = ""
        with pytest.raises(WhisperClientError, match="not configured"):
            fetch_whisper_transcript_sync("https://youtube.com/watch?v=x")

    @patch("kindly_web_search_mcp_server.youtube.whisper_client.settings")
    def test_timeout_raises(self, mock_settings: MagicMock) -> None:
        """httpx timeout → WhisperClientError."""
        mock_settings.whisper_space_url = "https://whisper.hf.space"
        mock_settings.whisper_space_timeout_seconds = 1.0

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_client_cls.return_value = mock_client

            with pytest.raises(WhisperClientError, match="timed out"):
                fetch_whisper_transcript_sync(
                    "https://youtube.com/watch?v=x",
                    timeout_seconds=1.0,
                )

    @patch("kindly_web_search_mcp_server.youtube.whisper_client.settings")
    def test_http_error_raises(self, mock_settings: MagicMock) -> None:
        """HTTP 503 → WhisperClientError."""
        mock_settings.whisper_space_url = "https://whisper.hf.space"
        mock_settings.whisper_space_timeout_seconds = 300.0

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service Unavailable", request=MagicMock(), response=mock_resp
        )

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            with pytest.raises(WhisperClientError, match="HTTP 503"):
                fetch_whisper_transcript_sync(
                    "https://youtube.com/watch?v=x",
                    timeout_seconds=10.0,
                )


# ---------------------------------------------------------------------------
# Cascade integration
# ---------------------------------------------------------------------------


class TestCascadeWhisper:
    """Test cascade integration with Whisper layer."""

    def test_cascade_skips_whisper_without_url(self) -> None:
        """Cascade skips Whisper layer when WHISPER_SPACE_URL is empty."""
        from kindly_web_search_mcp_server.youtube.cascade import (
            fetch_transcript_cascade,
        )

        mock_segments = [{"text": "From API", "start": 0.0, "duration": 1.0}]
        with (
            patch("kindly_web_search_mcp_server.youtube.cascade.settings") as mock_settings,
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=Exception("yt-dlp fail"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
                return_value=mock_segments,
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.whisper_client.fetch_whisper_transcript_sync",
            ) as mock_whisper,
        ):
            mock_settings.whisper_space_url = ""
            mock_settings.youtube_transcript_backend = "auto"
            mock_settings.youtube_transcript_proxy_url = ""
            mock_settings.youtube_transcript_max_chars = 50000
            mock_settings.youtube_transcript_timeout_seconds = 30

            segments, backend = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert backend == "api"
            mock_whisper.assert_not_called()

    def test_cascade_uses_whisper(self) -> None:
        """Cascade uses Whisper when yt-dlp fails and Whisper succeeds."""
        from kindly_web_search_mcp_server.youtube.cascade import (
            fetch_transcript_cascade,
        )

        whisper_segments = [{"text": "Whisper text", "start": 0.0, "duration": 5.0}]
        with (
            patch("kindly_web_search_mcp_server.youtube.cascade.settings") as mock_settings,
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.ytdlp_extract_subtitles",
                side_effect=Exception("yt-dlp fail"),
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.whisper_client.fetch_whisper_transcript_sync",
                return_value=whisper_segments,
            ),
            patch(
                "kindly_web_search_mcp_server.youtube.cascade.fetch_transcript_data",
            ) as mock_api,
        ):
            mock_settings.whisper_space_url = "https://whisper.hf.space"
            mock_settings.whisper_space_timeout_seconds = 300.0
            mock_settings.youtube_transcript_backend = "auto"
            mock_settings.youtube_transcript_proxy_url = ""
            mock_settings.youtube_transcript_max_chars = 50000
            mock_settings.youtube_transcript_timeout_seconds = 30

            segments, backend = fetch_transcript_cascade("dQw4w9WgXcQ", backend="auto")
            assert backend == "whisper"
            assert segments == whisper_segments
            mock_api.assert_not_called()
