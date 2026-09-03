"""Tests for channel target detection and video ID validation."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.youtube.models import YouTubeError
from kindly_web_search_mcp_server.youtube.url_parser import (
    looks_like_channel_target,
    parse_youtube_url,
)


@pytest.mark.parametrize(
    "target",
    [
        "@somehandle",
        "UC12345678901234567890AB",
        "https://www.youtube.com/@somehandle",
        "https://www.youtube.com/channel/UC12345678901234567890AB",
        "youtube.com/@handle",
    ],
)
def test_looks_like_channel_target_true(target: str) -> None:
    assert looks_like_channel_target(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "not a channel",
        "https://example.com/@x",
    ],
)
def test_looks_like_channel_target_false(target: str) -> None:
    assert looks_like_channel_target(target) is False


def test_invalid_bare_id_reports_clear_error() -> None:
    with pytest.raises(YouTubeError) as exc:
        parse_youtube_url("notarealvideoid00")
    assert "Invalid YouTube video ID" in str(exc.value)


def test_channel_id_reports_video_requirement() -> None:
    with pytest.raises(YouTubeError) as exc:
        parse_youtube_url("UC12345678901234567890AB")
    assert "YouTube video ID or URL" in str(exc.value)
