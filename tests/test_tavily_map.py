from __future__ import annotations

import json
import os
import unittest

import anyio
import httpx


class TestTavilyMapClient(unittest.TestCase):
    def test_map_site_parses_results(self) -> None:
        async def run() -> None:
            from kindly_web_search_mcp_server.content import tavily_map as tavily_map_module

            os.environ["TAVILY_API_KEY"] = "tvly_test"
            tavily_map_module.settings.tavily_api_key = "tvly_test"
            from kindly_web_search_mcp_server.content.tavily_map import map_site

            tavily_payload = {
                "base_url": "docs.tavily.com",
                "results": [
                    "https://docs.tavily.com/welcome",
                    "https://docs.tavily.com/documentation/api-credits",
                ],
                "response_time": 1.23,
                "usage": {"credits": 1},
                "request_id": "123e4567-e89b-12d3-a456-426614174111",
            }

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), "https://api.tavily.com/map")
                self.assertEqual(request.headers.get("authorization"), "Bearer tvly_test")
                body = json.loads(request.content.decode())
                self.assertEqual(body["url"], "https://docs.tavily.com")
                self.assertEqual(body["max_depth"], 1)
                self.assertEqual(body["max_breadth"], 20)
                self.assertEqual(body["limit"], 50)
                return httpx.Response(200, json=tavily_payload)

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                result = await map_site(
                    "https://docs.tavily.com",
                    max_depth=1,
                    max_breadth=20,
                    limit=50,
                    http_client=client,
                )

            self.assertEqual(result["base_url"], "docs.tavily.com")
            self.assertEqual(result["results"], tavily_payload["results"])
            self.assertEqual(result["response_time"], 1.23)
            self.assertEqual(result["usage"], {"credits": 1})
            self.assertEqual(result["request_id"], "123e4567-e89b-12d3-a456-426614174111")

        anyio.run(run)


if __name__ == "__main__":
    unittest.main()
