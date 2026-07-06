from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.search.reddit import search_reddit


class _DummyResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class TestRedditUnit(unittest.IsolatedAsyncioTestCase):
    @patch("kindly_web_search_mcp_server.search.reddit.settings")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_reddit_delay_is_configurable(self, mock_sleep: AsyncMock, mock_settings: object) -> None:

        mock_settings.reddit_delay_seconds = 0.25
        http_client = type("Client", (), {})()
        http_client.get = AsyncMock(
            return_value=_DummyResponse(
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "title": "R",
                                    "url": "https://example.com/r",
                                    "subreddit": "python",
                                    "score": 5,
                                    "num_comments": 1,
                                }
                            }
                        ]
                    }
                }
            )
        )

        results = await search_reddit(
            "httpx",
            num_results=1,
            http_client=http_client,  # type: ignore[arg-type]
        )

        self.assertEqual(results[0].title, "R")
        mock_sleep.assert_awaited_once_with(0.25)
