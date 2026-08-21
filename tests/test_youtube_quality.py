from __future__ import annotations

from kindly_web_search_mcp_server.youtube.quality import (
    normalize_transcript_segments,
    truncate_segments,
)


def test_normalize_transcript_segments_repairs_and_deduplicates() -> None:
    segments, quality = normalize_transcript_segments(
        [
            {"text": "  Hello\u200b world  ", "start": 2, "duration": 1},
            {"text": "Hello world", "start": 2, "duration": 1},
            {"text": "Invalid", "start": "bad", "duration": 1},
            {"text": "Earlier", "start": 0, "duration": 1},
        ]
    )

    assert [item["text"] for item in segments] == ["Earlier", "Hello world"]
    assert quality.segment_count == 2
    assert quality.duplicate_segments_removed == 1
    assert quality.malformed_segments_removed == 1
    assert quality.word_count == 3


def test_truncate_segments_keeps_segment_boundaries() -> None:
    segments = [
        {"text": "one two", "start": 0.0, "duration": 1.0},
        {"text": "three four", "start": 1.0, "duration": 1.0},
    ]

    result, truncated = truncate_segments(segments, 8)

    assert result == [segments[0]]
    assert truncated is True
