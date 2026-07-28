"""Tests for Cloudflare Workers AI Whisper backend."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.youtube.cf_whisper import (
    CfWhisperError,
    _parse_vtt_timestamp,
    _parse_cloudflare_response,
)


class TestParseVttTimestamp:
    """Test VTT timestamp parsing."""

    def test_full_timestamp(self) -> None:
        assert _parse_vtt_timestamp("00:00:01.500") == 1.5

    def test_hours(self) -> None:
        assert _parse_vtt_timestamp("01:30:00.000") == 5400.0

    def test_milliseconds(self) -> None:
        assert _parse_vtt_timestamp("00:00:00.123") == 0.123

    def test_zero(self) -> None:
        assert _parse_vtt_timestamp("00:00:00.000") == 0.0

    def test_invalid_format(self) -> None:
        assert _parse_vtt_timestamp("invalid") == 0.0

    def test_empty_string(self) -> None:
        assert _parse_vtt_timestamp("") == 0.0


class TestParseCloudflareResponse:
    """Test Cloudflare API response parsing."""

    def test_successful_response(self) -> None:
        data = {
            "success": True,
            "result": {
                "segments": [
                    {
                        "vtt": "00:00:00.000 --> 00:00:01.000\nHello world",
                        "text": "Hello world",
                        "start": 0.0,
                        "end": 1.0,
                        "word_count": 2,
                    },
                    {
                        "vtt": "00:00:01.000 --> 00:00:03.500\nHow are you today",
                        "text": "How are you today",
                        "start": 1.0,
                        "end": 3.5,
                        "word_count": 4,
                    },
                ]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 2
        assert segments[0]["text"] == "Hello world"
        assert segments[0]["start"] == 0.0
        assert segments[0]["duration"] == 1.0
        assert segments[1]["text"] == "How are you today"
        assert segments[1]["start"] == 1.0
        assert segments[1]["duration"] == 2.5

    def test_response_without_vtt_fallback_to_numeric(self) -> None:
        data = {
            "success": True,
            "result": {
                "segments": [
                    {"text": "Hello", "start": 0.0, "end": 0.5},
                    {"text": "World", "start": 0.5, "end": 1.0},
                ]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 2
        assert segments[0]["text"] == "Hello"
        assert segments[0]["start"] == 0.0
        assert segments[0]["duration"] == 0.5

    def test_success_false_raises(self) -> None:
        data = {"success": False, "errors": [{"message": "Model not found"}]}
        with pytest.raises(CfWhisperError, match="success=false"):
            _parse_cloudflare_response(data)

    def test_missing_result_raises(self) -> None:
        data = {"success": True}
        with pytest.raises(CfWhisperError, match="result is not a dict"):
            _parse_cloudflare_response(data)

    def test_empty_segments(self) -> None:
        data = {"success": True, "result": {"segments": []}}
        segments = _parse_cloudflare_response(data)
        assert segments == []

    def test_empty_text_skipped(self) -> None:
        data = {
            "success": True,
            "result": {
                "segments": [
                    {"text": "Hello", "start": 0.0, "end": 1.0},
                    {"text": "", "start": 1.0, "end": 2.0},
                    {"text": "   ", "start": 2.0, "end": 3.0},
                    {"text": "World", "start": 3.0, "end": 4.0},
                ]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 2
        assert segments[0]["text"] == "Hello"
        assert segments[1]["text"] == "World"

    def test_missing_segments_list_raises(self) -> None:
        data = {"success": True, "result": {}}
        with pytest.raises(CfWhisperError, match="missing segments"):
            _parse_cloudflare_response(data)

    def test_non_dict_data_raises(self) -> None:
        with pytest.raises(CfWhisperError, match="non-dict"):
            _parse_cloudflare_response("not a dict")  # type: ignore[arg-type]

    def test_non_list_segments_raises(self) -> None:
        data = {"success": True, "result": {"segments": "not a list"}}
        with pytest.raises(CfWhisperError, match="missing segments"):
            _parse_cloudflare_response(data)

    def test_partial_vtt_timestamp(self) -> None:
        """VTT line without proper arrow format."""
        data = {
            "success": True,
            "result": {
                "segments": [{"vtt": "just some text", "text": "Hello", "start": 0.0, "end": 1.0}]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 1
        assert segments[0]["start"] == 0.0
        assert segments[0]["duration"] == 1.0

    def test_translate_response(self) -> None:
        """Translation response has same format as transcription."""
        data = {
            "success": True,
            "result": {
                "segments": [
                    {"text": "Bonjour le monde", "start": 0.0, "end": 1.5},
                ]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 1
        assert segments[0]["text"] == "Bonjour le monde"
        assert segments[0]["duration"] == 1.5

    def test_large_word_count_segment(self) -> None:
        data = {
            "success": True,
            "result": {
                "segments": [
                    {
                        "vtt": "00:00:10.000 --> 00:00:15.500\nThis is a longer segment with multiple words",
                        "text": "This is a longer segment with multiple words",
                        "start": 10.0,
                        "end": 15.5,
                        "word_count": 8,
                    }
                ]
            },
        }
        segments = _parse_cloudflare_response(data)
        assert len(segments) == 1
        assert segments[0]["start"] == 10.0
        assert segments[0]["duration"] == 5.5
        assert segments[0]["text"] == "This is a longer segment with multiple words"


class TestCfWhisperIntegration:
    """Minimal cascade integration tests for cf_whisper backend."""

    def test_invalid_backend_raises_value_error(self) -> None:
        """Verify _VALID_BACKENDS includes cf_whisper."""
        from kindly_web_search_mcp_server.youtube.cascade import fetch_transcript_cascade

        with pytest.raises(ValueError, match="Unknown backend"):
            fetch_transcript_cascade("dQw4w9WgXcQ", backend="invalid_backend")

    def test_cf_whisper_skipped_when_not_configured(self) -> None:
        """Verify cascade skips cf_whisper when credentials not set."""
        from kindly_web_search_mcp_server.youtube.cascade import fetch_transcript_cascade

        with pytest.raises(Exception) as excinfo:
            fetch_transcript_cascade("dQw4w9WgXcQ", backend="cf_whisper")
        assert "CLOUDFLARE_ACCOUNT_ID" in str(excinfo.value)

    def test_cf_whisper_backend_name_in_valid_backends(self) -> None:
        """Verify 'cf_whisper' is in the valid backends list."""
        from kindly_web_search_mcp_server.youtube.cascade import _VALID_BACKENDS

        assert "cf_whisper" in _VALID_BACKENDS
