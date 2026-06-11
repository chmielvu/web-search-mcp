from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.search.stackexchange import search_stackexchange


class _DummyResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class TestStackExchangeUnit(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        os.environ,
        {"STACKEXCHANGE_SITES": "stackoverflow;superuser"},
        clear=False,
    )
    async def test_stackexchange_sites_are_configurable(self) -> None:
        http_client = type("Client", (), {})()
        http_client.get = AsyncMock(
            return_value=_DummyResponse(
                {
                    "quota_remaining": 100,
                    "items": [
                        {
                            "title": "T",
                            "link": "https://example.com",
                            "score": 1,
                            "answer_count": 2,
                            "tags": ["python"],
                        }
                    ],
                }
            )
        )

        results = await search_stackexchange(
            "python httpx",
            num_results=1,
            http_client=http_client,  # type: ignore[arg-type]
        )

        self.assertEqual(results[0].title, "T")
        self.assertEqual(
            http_client.get.await_args.kwargs["params"]["site"],
            "stackoverflow;superuser",
        )
