"""Tests for Whisper VPS service client and cascade integration."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from kindly_web_search_mcp_server.youtube.vps_whisper import (
    VpsWhisperError,
    _build_vps_endpoint,
    _parse_vps_response,
    fetch_vps_whisper_transcript_sync,
)


class TestVpsWhisperResponseParsing:
    def test_parse_segments_format(self) -> None:
        data = {
            "segments": [
                {"text": "Hello world", "start": 0.0, "duration": 2.5},
                {"text": "Second line", "start": 2.5, "duration": 1.5},
            ]
        }
        segments = _parse_vps_response(data)
        assert len(segments) == 2
        assert segments[0]["text"] == "Hello world"
        assert segments[0]["start"] == 0.0
        assert segments[1]["text"] == "Second line"

    def test_parse_fallback_text(self) -> None:
        data = {"text": "Full transcript paragraph"}
        segments = _parse_vps_response(data)
        assert len(segments) == 1
        assert segments[0]["text"] == "Full transcript paragraph"

    def test_parse_gradio_wrapper(self) -> None:
        data = {"data": ['{"segments": [{"text": "Gradio segment", "start": 1.0, "duration": 2.0}]}']}
        segments = _parse_vps_response(data)
        assert len(segments) == 1
        assert segments[0]["text"] == "Gradio segment"

    def test_empty_response_raises(self) -> None:
        with pytest.raises(VpsWhisperError, match="no valid transcript segments"):
            _parse_vps_response({})


class TestVpsEndpointBuilding:
    def test_build_endpoint_default(self) -> None:
        url = _build_vps_endpoint("http://127.0.0.1:8000")
        assert url == "http://127.0.0.1:8000/transcribe"

    def test_build_endpoint_existing_transcribe(self) -> None:
        url = _build_vps_endpoint("http://127.0.0.1:8000/transcribe")
        assert url == "http://127.0.0.1:8000/transcribe"


class TestVpsWhisperSync:
    @patch("kindly_web_search_mcp_server.youtube.vps_whisper.settings")
    def test_unconfigured_url_raises(self, mock_settings: MagicMock) -> None:
        mock_settings.whisper_vps_url = ""
        with pytest.raises(VpsWhisperError, match="WHISPER_VPS_URL is not configured"):
            fetch_vps_whisper_transcript_sync("dQw4w9WgXcQ")

    @patch("httpx.Client")
    @patch("kindly_web_search_mcp_server.youtube.vps_whisper.settings")
    def test_successful_fetch(self, mock_settings: MagicMock, mock_client_cls: MagicMock) -> None:
        mock_settings.whisper_vps_url = "http://127.0.0.1:8000"
        mock_settings.whisper_vps_timeout_seconds = 300.0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "segments": [{"text": "Test segment", "start": 0.0, "duration": 5.0}]
        }
        mock_resp.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        segments = fetch_vps_whisper_transcript_sync("dQw4w9WgXcQ")
        assert len(segments) == 1
        assert segments[0]["text"] == "Test segment"


class TestVpsWhisperCascadeIntegration:
    def test_valid_backends_includes_vps_whisper(self) -> None:
        from kindly_web_search_mcp_server.youtube.cascade import _VALID_BACKENDS

        assert "vps_whisper" in _VALID_BACKENDS

    @patch("kindly_web_search_mcp_server.youtube.cascade.settings")
    def test_vps_whisper_backend_unconfigured_raises(self, mock_settings: MagicMock) -> None:
        from kindly_web_search_mcp_server.youtube.cascade import fetch_transcript_cascade

        mock_settings.whisper_vps_url = ""
        with pytest.raises(Exception, match="WHISPER_VPS_URL"):
            fetch_transcript_cascade("dQw4w9WgXcQ", backend="vps_whisper")
