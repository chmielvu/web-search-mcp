from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"summary":"Test summary","key_points":["Point"],'
                            '"important_entities":[],"verbatim_terms":["GLM-5"],"limitations":[]}'
                        )
                    }
                }
            ]
        }


class TestChutesSummary(unittest.IsolatedAsyncioTestCase):
    async def test_create_summary_calls_chutes_with_env_token(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=_FakeResponse())

        with patch("httpx.AsyncClient") as mock_client_cls, patch.dict(
            os.environ, {"CHUTES_API_TOKEN": "token"}, clear=False
        ):
            mock_client_cls.return_value.__aenter__.return_value = fake_client
            result = await create_summary(
                "Source text about GLM-5 and web fetch.",
                mode="brief",
                focus_query="GLM-5",
            )

        self.assertEqual(result["summary"], "Test summary")
        self.assertEqual(result["model"], "zai-org/GLM-5-Turbo")
        _, kwargs = fake_client.post.await_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(kwargs["json"]["stream"], False)
        self.assertEqual(kwargs["json"]["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()

