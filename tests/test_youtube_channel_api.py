from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kindly_web_search_mcp_server.youtube.channel_api import list_channel_videos


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = [_Response(response) for response in responses]
        self.calls: list[dict] = []

    async def get(self, url: str, *, params: dict, timeout: float) -> _Response:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_channel_enumeration_uses_uploads_playlist_pagination(monkeypatch) -> None:
    client = _Client(
        [
            {
                "items": [
                    {
                        "id": "UC12345678901234567890",
                        "snippet": {"title": "Example Channel"},
                        "contentDetails": {
                            "relatedPlaylists": {"uploads": "UU12345678901234567890"}
                        },
                    }
                ]
            },
            {
                "items": [
                    {
                        "snippet": {
                            "title": "Video 1",
                            "resourceId": {"videoId": "abcdefghijk"},
                            "publishedAt": "2026-01-01T00:00:00Z",
                            "position": 0,
                        }
                    }
                ],
                "nextPageToken": "next",
            },
            {
                "items": [
                    {
                        "snippet": {
                            "title": "Video 2",
                            "resourceId": {"videoId": "lmnopqrstuv"},
                            "position": 1,
                        }
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.youtube.channel_api.settings.youtube_api_key",
        "test-key",
    )
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.youtube.channel_api.settings.youtube_api_timeout_seconds",
        5.0,
    )
    tracker = MagicMock()
    tracker.can_afford.return_value = True
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.youtube.channel_api.get_youtube_api_quota_tracker",
        lambda: tracker,
    )

    channel_id, videos, next_token = await list_channel_videos(
        "@example",
        max_results=2,
        http_client=client,  # type: ignore[arg-type]
    )

    assert channel_id == "UC12345678901234567890"
    assert [video.video_id for video in videos] == ["abcdefghijk", "lmnopqrstuv"]
    assert next_token is None
    assert client.calls[0]["params"]["forHandle"] == "example"
    assert client.calls[1]["params"]["playlistId"] == "UU12345678901234567890"
    assert client.calls[2]["params"]["pageToken"] == "next"
    assert tracker.record_call.call_count == 3
