from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.text = json.dumps(payload)


class _FakeModels:
    def __init__(self, effects: list[Any]) -> None:
        self.effects = effects
        self.calls: list[tuple[str, str, object]] = []

    def generate_content(self, *, model: str, contents: str, config: object) -> _FakeResponse:
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
    async def test_create_summary_disabled_by_default(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        result = await create_summary("Local page text")

        self.assertIsNone(result)

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
                ai_summary=True,
                focus_query="Gemini",
                source_urls=["https://example.com/article"],
            )

        self.assertEqual(result["summary"], "Test summary")
        self.assertEqual(result["model"], "gemini-3.5-flash-lite")
        self.assertEqual(result["model_used"], "gemini-3.5-flash-lite")
        self.assertEqual(result["input_tokens"], 14)
        self.assertEqual(result["output_tokens"], 8)
        self.assertEqual(result["backend"], "gemini-api")
        self.assertEqual(result["provider"], "google")

        model, contents, config = fake_client.models.calls[0]
        self.assertEqual(model, "gemini-3.5-flash-lite")
        self.assertIn("example.com/article", contents)
        self.assertIn("<source_text>", contents)
        self.assertIn("Local page text", contents)
        tools = getattr(config, "tools", None)
        self.assertFalse(tools)
        self.assertEqual(getattr(config, "response_mime_type", None), "application/json")

    async def test_create_summary_uses_url_context_when_body_empty(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        fake_client = _FakeClient(
            [
                _FakeResponse(
                    {
                        "summary": "URL context summary",
                        "key_points": ["Point"],
                        "important_entities": [],
                        "verbatim_terms": [],
                        "limitations": [],
                    }
                )
            ]
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
                "",
                ai_summary=True,
                source_urls=["https://example.com/article"],
            )

        self.assertEqual(result["summary"], "URL context summary")
        _, contents, config = fake_client.models.calls[0]
        self.assertTrue(getattr(config, "tools", None))
        self.assertIsNotNone(getattr(config.tools[0], "url_context", None))
        self.assertNotIn("<source_text>", contents)

    async def test_create_summary_falls_back_through_gemini_31_to_gemma(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        fake_client = _FakeClient(
            [
                RuntimeError("primary unavailable"),
                RuntimeError("Gemini 3.1 unavailable"),
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
        fake_client.models.effects[2].usage_metadata = SimpleNamespace(
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
                ai_summary=True,
                focus_query="Gemma fallback",
                source_urls=["https://example.com/article"],
            )

        self.assertEqual(result["summary"], "Fallback summary")
        self.assertEqual(result["model"], "gemma-4-26b-a4b-it")
        self.assertEqual(result["model_used"], "gemma-4-26b-a4b-it")
        self.assertEqual(result["input_tokens"], 13)
        self.assertEqual(result["output_tokens"], 6)
        self.assertEqual(result["backend"], "gemma-fallback")
        self.assertEqual(len(fake_client.models.calls), 3)
        self.assertEqual(fake_client.models.calls[0][0], "gemini-3.5-flash-lite")
        self.assertEqual(fake_client.models.calls[1][0], "gemini-3.1-flash-lite")
        self.assertEqual(fake_client.models.calls[2][0], "gemma-4-26b-a4b-it")
        self.assertFalse(getattr(fake_client.models.calls[2][2], "tools", None))

    async def test_create_batch_summaries_uses_single_batched_call(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_batch_summaries

        fake_client = _FakeClient(
            [
                _FakeResponse(
                    {
                        "summaries": [
                            {
                                "url": "https://example.com/one",
                                "summary": "First summary",
                                "key_points": ["A"],
                                "important_entities": [],
                                "verbatim_terms": [],
                                "limitations": [],
                            },
                            {
                                "url": "https://example.com/two",
                                "summary": "Second summary",
                                "key_points": ["B"],
                                "important_entities": [],
                                "verbatim_terms": [],
                                "limitations": [],
                            },
                        ]
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
            patch.dict(
                os.environ,
                {"GEMINI_API_KEY": "primary-token", "GEMINI_SECOND_API_KEY": "paid-token"},
                clear=False,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend.genai.Client",
                return_value=fake_client,
            ) as mock_client,
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._client",
                None,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._batch_client",
                None,
            ),
        ):
            summaries = await create_batch_summaries(
                items,
                ai_summary=True,
                focus_query="batch",
                max_concurrency=2,
            )

        self.assertEqual(
            [summary["summary"] for summary in summaries if summary],
            ["First summary", "Second summary"],
        )
        # Exactly one Gemini call should have been made.
        self.assertEqual(len(fake_client.models.calls), 1)
        # It should use the paid key.
        mock_client.assert_called_once_with(api_key="paid-token")
        # Both URLs should be in the prompt.
        contents = fake_client.models.calls[0][1]
        self.assertIn("https://example.com/one", contents)
        self.assertIn("https://example.com/two", contents)
        self.assertIn("summaries", contents)

    async def test_create_batch_summaries_fallback_uses_second_key(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_batch_summaries

        # Batch call fails; two per-item calls succeed.
        fake_client = _FakeClient(
            [
                RuntimeError("batch failed"),
                RuntimeError("Gemini 3.1 batch fallback failed"),
                RuntimeError("Gemma batch fallback failed"),
                _FakeResponse(
                    {
                        "summary": "First fallback summary",
                    }
                ),
                _FakeResponse(
                    {
                        "summary": "Second fallback summary",
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
            patch.dict(
                os.environ,
                {"GEMINI_API_KEY": "primary-token", "GEMINI_SECOND_API_KEY": "paid-token"},
                clear=False,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend.genai.Client",
                return_value=fake_client,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._client",
                None,
            ),
            patch(
                "kindly_web_search_mcp_server.content.summary_backend._batch_client",
                None,
            ),
        ):
            summaries = await create_batch_summaries(
                items,
                ai_summary=True,
                focus_query="batch",
                max_concurrency=2,
            )

        self.assertEqual(
            [summary["summary"] for summary in summaries if summary],
            ["First fallback summary", "Second fallback summary"],
        )
        self.assertEqual(
            [call[0] for call in fake_client.models.calls],
            [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemma-4-26b-a4b-it",
                "gemini-3.5-flash-lite",
                "gemini-3.5-flash-lite",
            ],
        )

    async def test_per_item_summary_passes_page_content(self) -> None:
        from kindly_web_search_mcp_server.content.summary_backend import _per_item_summary
        from kindly_web_search_mcp_server.content.summary_models import SummaryOutput

        mock_output = SummaryOutput(
            summary="Item summary",
            key_points=["Key 1"],
            important_entities=[],
            verbatim_terms=[],
            limitations=[],
        )
        item = {
            "input_url": "https://example.com/item",
            "page_content": "Detailed page markdown content",
        }
        with patch(
            "kindly_web_search_mcp_server.content.summary_backend._generate_summary",
            new=AsyncMock(return_value=(mock_output, None)),
        ) as mock_gen:
            res = await _per_item_summary(item, mode="detailed", focus_query=None)
            self.assertEqual(res["summary"], "Item summary")
            mock_gen.assert_awaited_once()
            _, kwargs = mock_gen.call_args
            self.assertEqual(kwargs["source_text"], "Detailed page markdown content")
            self.assertFalse(kwargs["use_url_context"])

    def test_drop_inaccessible_claim_on_long_body(self) -> None:
        from kindly_web_search_mcp_server.content.summary_backend import (
            _drop_inaccessible_claim,
        )

        body = "word " * 90
        dropped = _drop_inaccessible_claim(
            {
                "summary": "The page was inaccessible and could not retrieve the article.",
                "key_points": ["Missing"],
                "limitations": ["blocked or inaccessible"],
            },
            body,
        )
        self.assertEqual(
            dropped["summary"],
            "Source text was present but the model failed to summarize it.",
        )
        self.assertEqual(dropped["key_points"], [])
        self.assertIn("model_claimed_inaccessible_with_body", dropped["limitations"])

        kept = _drop_inaccessible_claim(
            {"summary": "A flooding report.", "key_points": ["Rain"], "limitations": []},
            body,
        )
        self.assertEqual(kept["summary"], "A flooding report.")

    def test_batch_prompt_uses_source_text_not_url_context(self) -> None:
        from kindly_web_search_mcp_server.content.summary_backend import (
            _build_batch_user_prompt,
            _make_batch_config,
        )

        items = [
            {
                "fetched_url": "https://example.com/one",
                "page_content": "Local page text one",
            },
            {
                "fetched_url": "https://example.com/two",
                "page_content": "Local page text two",
            },
        ]
        prompt = _build_batch_user_prompt(mode="detailed", focus_query=None, items=items)
        self.assertIn("<source_text>", prompt)
        self.assertIn("Local page text one", prompt)
        self.assertIn("Do not fetch URLs", prompt)
        self.assertNotIn("Use the URL context tool", prompt)
        config = _make_batch_config(max_output_tokens=1200)
        self.assertFalse(getattr(config, "tools", None))


if __name__ == "__main__":
    unittest.main()
