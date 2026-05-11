from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _fake_grounding_response() -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text="FastMCP docs explain middleware support and installation steps.",
                            thought=False,
                        ),
                    ]
                ),
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(
                            web=SimpleNamespace(
                                uri="https://gofastmcp.com/docs/middleware",
                                title="FastMCP Middleware Docs",
                            )
                        ),
                        SimpleNamespace(
                            web=SimpleNamespace(
                                uri="https://github.com/jlowin/fastmcp",
                                title="FastMCP GitHub",
                            )
                        ),
                    ],
                    grounding_supports=[
                        SimpleNamespace(
                            segment=SimpleNamespace(
                                text="FastMCP docs explain middleware support and installation steps."
                            ),
                            grounding_chunk_indices=[0],
                        ),
                        SimpleNamespace(
                            segment=SimpleNamespace(
                                text="The FastMCP GitHub repository contains implementation details and examples."
                            ),
                            grounding_chunk_indices=[1],
                        ),
                    ],
                ),
            )
        ]
    )


class TestGeminiProviderSearch(unittest.IsolatedAsyncioTestCase):
    async def test_search_gemini_returns_grounded_results_with_snippets(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search import search_gemini

        client = MagicMock()
        client.models.generate_content.return_value = _fake_grounding_response()

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch("kindly_web_search_mcp_server.search.gemini_search.settings.gemini_api_key", "test-key"),
            patch(
                "kindly_web_search_mcp_server.search.gemini_provider_grounding.get_gemini_client",
                return_value=client,
            ),
        ):
            results = await search_gemini("fastmcp middleware docs", num_results=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "FastMCP Middleware Docs")
        self.assertEqual(results[0].link, "https://gofastmcp.com/docs/middleware")
        self.assertIn("middleware support", results[0].snippet.lower())
        self.assertEqual(results[1].providers, ["gemini"])

    async def test_search_gemini_falls_back_to_answer_text(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search import search_gemini

        response = _fake_grounding_response()
        response.candidates[0].grounding_metadata.grounding_supports = []

        client = MagicMock()
        client.models.generate_content.return_value = response

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch("kindly_web_search_mcp_server.search.gemini_search.settings.gemini_api_key", "test-key"),
            patch(
                "kindly_web_search_mcp_server.search.gemini_provider_grounding.get_gemini_client",
                return_value=client,
            ),
        ):
            results = await search_gemini("fastmcp middleware docs", num_results=1)

        self.assertEqual(len(results), 1)
        self.assertIn("middleware support", results[0].snippet.lower())

    async def test_search_gemini_returns_empty_without_key(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search import search_gemini

        with patch("kindly_web_search_mcp_server.search.gemini_search.settings.gemini_api_key", ""):
            results = await search_gemini("fastmcp middleware docs", num_results=2)

        self.assertEqual(results, [])

    async def test_search_gemini_handles_client_error(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search import search_gemini

        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("boom")

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch("kindly_web_search_mcp_server.search.gemini_search.settings.gemini_api_key", "test-key"),
            patch(
                "kindly_web_search_mcp_server.search.gemini_provider_grounding.get_gemini_client",
                return_value=client,
            ),
        ):
            results = await search_gemini("fastmcp middleware docs", num_results=2)

        self.assertEqual(results, [])

    async def test_search_gemini_unwraps_grounding_redirect_urls(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search import search_gemini

        response = _fake_grounding_response()
        response.candidates[0].grounding_metadata.grounding_chunks[0].web.uri = (
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/example"
        )
        response.candidates[0].grounding_metadata.grounding_chunks[0].web.title = "google.com"

        client = MagicMock()
        client.models.generate_content.return_value = response
        redirect_response = MagicMock()
        redirect_response.url = "https://docs.example.com/middleware"
        redirect_response.text = "<html><head><title>FastMCP Middleware Guide</title></head></html>"

        async_client = MagicMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=None)
        async_client.get = AsyncMock(return_value=redirect_response)

        with (
            patch.dict(os.environ, {"KINDLY_GEMINI_API_KEY": "test-key"}, clear=False),
            patch("kindly_web_search_mcp_server.search.gemini_search.settings.gemini_api_key", "test-key"),
            patch(
                "kindly_web_search_mcp_server.search.gemini_provider_grounding.get_gemini_client",
                return_value=client,
            ),
            patch(
                "kindly_web_search_mcp_server.search.gemini_provider_grounding.httpx.AsyncClient",
                return_value=async_client,
            ),
        ):
            results = await search_gemini("fastmcp middleware docs", num_results=1)

        self.assertEqual(results[0].link, "https://docs.example.com/middleware")
        self.assertEqual(results[0].title, "FastMCP Middleware Guide")


class TestGeminiSettings(unittest.TestCase):
    def test_gemini_provider_mode_defaults_to_always(self) -> None:
        from kindly_web_search_mcp_server.settings import Settings

        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        self.assertEqual(settings.gemini_mode, "always")


if __name__ == "__main__":
    unittest.main()
