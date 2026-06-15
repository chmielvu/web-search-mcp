from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.text = json.dumps(payload)


class _FakeModels:
    def __init__(self, effects: list[Any]) -> None:
        self.effects = effects
        self.calls: list[tuple[str, str, object]] = []

    def generate_content(
        self, *, model: str, contents: str, config: object
    ) -> _FakeResponse:
        self.calls.append((model, contents, config))
        if not self.effects:
            raise AssertionError("No fake response configured")
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, effects: list[Any]) -> None:
        self.models = _FakeModels(effects)


class TestGeminiSummary(unittest.IsolatedAsyncioTestCase):
    async def test_create_summary_uses_url_context_on_primary_model(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        fake_client = _FakeClient(
            [
                _FakeResponse(
                    {
                        "summary": "Test summary",
                        "key_points": ["Point"],
                        "important_entities": [],
                        "verbatim_terms": ["Gemini 3.1"],
                        "limitations": [],
                    }
                )
            ]
        )
        fake_client.models.effects[0].usage_metadata = SimpleNamespace(
            prompt_token_count=14,
            response_token_count=8,
            total_token_count=22,
        )

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "token"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend.genai.Client",
                return_value=fake_client,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._client",
                None,
            ),
        ):
            result = await create_summary(
                "Local page text",
                mode="brief",
                focus_query="Gemini",
                source_urls=["https://example.com/article"],
            )

        self.assertEqual(result["summary"], "Test summary")
        self.assertEqual(result["model"], "gemini-3.1-flash-lite")
        self.assertEqual(result["model_used"], "gemini-3.1-flash-lite")
        self.assertEqual(result["input_tokens"], 14)
        self.assertEqual(result["output_tokens"], 8)
        self.assertEqual(result["backend"], "gemini-api")

        model, contents, config = fake_client.models.calls[0]
        self.assertEqual(model, "gemini-3.1-flash-lite")
        self.assertIn("example.com/article", contents)
        self.assertTrue(getattr(config, "tools", None))
        self.assertIsNotNone(getattr(config.tools[0], "url_context", None))
        self.assertEqual(
            getattr(config, "response_mime_type", None), "application/json"
        )

    async def test_create_summary_falls_back_to_gemma_when_primary_fails(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        fake_client = _FakeClient(
            [
                RuntimeError("primary unavailable"),
                _FakeResponse(
                    {
                        "summary": "Fallback summary",
                        "key_points": ["Fallback point"],
                        "important_entities": [],
                        "verbatim_terms": [],
                        "limitations": ["Used fallback model"],
                    }
                ),
            ]
        )
        fake_client.models.effects[1].usage_metadata = SimpleNamespace(
            prompt_token_count=13,
            response_token_count=6,
            total_token_count=19,
        )

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "token"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend.genai.Client",
                return_value=fake_client,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._client",
                None,
            ),
        ):
            result = await create_summary(
                "Local page text",
                mode="detailed",
                focus_query="Gemma fallback",
                source_urls=["https://example.com/article"],
            )

        self.assertEqual(result["summary"], "Fallback summary")
        self.assertEqual(result["model"], "gemma-4-26b-a4b-it")
        self.assertEqual(result["model_used"], "gemma-4-26b-a4b-it")
        self.assertEqual(result["input_tokens"], 13)
        self.assertEqual(result["output_tokens"], 6)
        self.assertEqual(result["backend"], "gemma-fallback")
        self.assertEqual(len(fake_client.models.calls), 2)
        self.assertEqual(fake_client.models.calls[0][0], "gemini-3.1-flash-lite")
        self.assertEqual(fake_client.models.calls[1][0], "gemma-4-26b-a4b-it")
        self.assertFalse(getattr(fake_client.models.calls[1][2], "tools", None))

    async def test_create_batch_summaries_attaches_per_item_summaries(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_batch_summaries

        fake_client = _FakeClient(
            [
                _FakeResponse(
                    {
                        "summary": "First summary",
                        "key_points": ["A"],
                        "important_entities": [],
                        "verbatim_terms": [],
                        "limitations": [],
                    }
                ),
                _FakeResponse(
                    {
                        "summary": "Second summary",
                        "key_points": ["B"],
                        "important_entities": [],
                        "verbatim_terms": [],
                        "limitations": [],
                    }
                ),
            ]
        )

        items = [
            {
                "input_url": "https://example.com/one",
                "normalized_url": "https://example.com/one",
                "fetched_url": "https://example.com/one",
                "page_content": "one",
            },
            {
                "input_url": "https://example.com/two",
                "normalized_url": "https://example.com/two",
                "fetched_url": "https://example.com/two",
                "page_content": "two",
            },
        ]

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "token"}, clear=False),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend.genai.Client",
                return_value=fake_client,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._client",
                None,
            ),
        ):
            summaries = await create_batch_summaries(
                items,
                mode="brief",
                focus_query="batch",
                max_concurrency=2,
            )

        self.assertEqual(
            [summary["summary"] for summary in summaries if summary],
            ["First summary", "Second summary"],
        )


if __name__ == "__main__":
    unittest.main()
