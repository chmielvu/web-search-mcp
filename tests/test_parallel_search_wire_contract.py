"""Wire-contract verification against the real `parallel` SDK.

Uses httpx.MockTransport so no network/quota is consumed, but exercises the
actual SDK client -> httpx -> response-mapping path. Verifies request shape,
SDK response mapping, and our _quick_web_search_impl mapping end to end.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestLiveShapeContract(unittest.TestCase):
    def test_wire_contract_matches_sdk_and_response(self) -> None:
        from parallel import AsyncParallel

        wire = {
            "search_id": "search_live_test",
            "session_id": "session_live_test",
            "results": [
                {
                    "url": "https://docs.parallel.ai/search",
                    "title": "Parallel Search Docs",
                    "publish_date": "2026-07-15",
                    "excerpts": ["First markdown excerpt.", "Second excerpt."],
                }
            ],
            "warnings": [
                {"type": "input_validation_warning", "message": "location ignored", "detail": None}
            ],
            "usage": [{"name": "sku_search_advanced", "count": 1}],
        }

        captured: list[dict[str, Any]] = []

        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=wire)

        async def run() -> None:
            from kindly_web_search_mcp_server.quick_web_search import _quick_web_search_impl

            transport = httpx.MockTransport(handler)
            client = AsyncParallel(
                api_key="pk_test", http_client=httpx.AsyncClient(transport=transport)
            )
            async with client:
                sdk_result = await client.search(
                    search_queries=["parallel web sdk"], objective="verify contract"
                )

            self.assertEqual(sdk_result.search_id, "search_live_test")
            self.assertEqual(len(sdk_result.results), 1)
            self.assertIsInstance(sdk_result.results[0].excerpts, list)
            self.assertEqual(sdk_result.results[0].title, "Parallel Search Docs")
            self.assertEqual(sdk_result.results[0].publish_date, "2026-07-15")
            self.assertEqual(len(sdk_result.warnings), 1)
            self.assertEqual(len(sdk_result.usage), 1)

            # Fresh client for the impl path (avoids "client closed" reuse issues)
            transport2 = httpx.MockTransport(handler)
            client2 = AsyncParallel(
                api_key="pk_test", http_client=httpx.AsyncClient(transport=transport2)
            )
            with (
                patch(
                    "parallel.AsyncParallel",
                    return_value=client2,
                ),
                patch("kindly_web_search_mcp_server.quick_web_search.settings") as mock_settings,
            ):
                mock_settings.parallel_api_key = "pk_test"
                response = await _quick_web_search_impl(
                    ["parallel web sdk"], objective="verify contract"
                )

            self.assertEqual(response.search_queries, ["parallel web sdk"])
            self.assertEqual(response.search_id, "search_live_test")
            self.assertEqual(response.session_id, "session_live_test")
            self.assertEqual(response.total_citations, 1)
            self.assertEqual(response.citations[0].title, "Parallel Search Docs")
            self.assertEqual(response.citations[0].publish_date, "2026-07-15")
            self.assertEqual(
                response.citations[0].snippet, "First markdown excerpt.\nSecond excerpt."
            )
            self.assertEqual(
                response.citations[0].excerpts, ["First markdown excerpt.", "Second excerpt."]
            )
            self.assertEqual(response.warnings[0]["type"], "input_validation_warning")
            self.assertEqual(response.usage[0]["name"], "sku_search_advanced")
            self.assertEqual(response.usage[0]["count"], 1)

            # Request 1 (bare SDK call): no mode in payload (SDK default applied)
            self.assertIn("search_queries", captured[0])
            self.assertIn("objective", captured[0])
            self.assertNotIn("mode", captured[0])
            # Request 2 (via impl): mode is locked to advanced
            self.assertEqual(captured[1].get("mode"), "advanced")
            self.assertNotIn("answer", captured[1])

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()
