from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.status: object | None = None
        self.exceptions: list[Exception] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)

    def set_status(self, status: object, description: str | None = None) -> None:
        self.status = (status, description)


class _FakeSpanCM:
    def __init__(self, span: _FakeSpan) -> None:
        self._span = span

    def __enter__(self) -> _FakeSpan:
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class TestAiSearchProviderTracing(unittest.IsolatedAsyncioTestCase):
    def _span_patch(self, target: str) -> tuple[object, _FakeSpan]:
        span = _FakeSpan()
        return patch(target, return_value=_FakeSpanCM(span)), span

    async def test_summary_create_summary_traces_gemini_call(self) -> None:
        from kindly_web_search_mcp_server.content.summary import create_summary

        patcher, span = self._span_patch(
            "kindly_web_search_mcp_server.content.summary_backend.create_llm_operation_span"
        )

        class FakeClient:
            def __init__(self) -> None:
                self.models = self
                self.calls: list[tuple[str, str, object]] = []

            def generate_content(
                self, *, model: str, contents: str, config: object
            ) -> object:
                self.calls.append((model, contents, config))

                class _Response:
                    usage_metadata = SimpleNamespace(
                        prompt_token_count=14,
                        response_token_count=8,
                        total_token_count=22,
                    )
                    text = json.dumps(
                        {
                            "summary": "short",
                            "key_points": ["one"],
                            "important_entities": [],
                            "verbatim_terms": [],
                            "limitations": [],
                        }
                    )

                return _Response()

        fake_client = FakeClient()

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-token"}, clear=False),
            patcher as mock_create,
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
                "hello world",
                mode="brief",
                focus_query="docs",
                source_urls=["https://example.com"],
            )

        self.assertEqual(result["model"], "gemini-3.1-flash-lite")
        self.assertEqual(result["model_used"], "gemini-3.1-flash-lite")
        self.assertEqual(result["input_tokens"], 14)
        self.assertEqual(result["output_tokens"], 8)
        self.assertEqual(span.attributes["summary.key_points_count"], 1)
        self.assertEqual(span.attributes["summary.important_entities_count"], 0)
        self.assertEqual(mock_create.call_args.kwargs["system"], "gemini")
        self.assertEqual(
            mock_create.call_args.kwargs["attributes"]["llm.model_name"],
            "gemini-3.1-flash-lite",
        )
        self.assertEqual(fake_client.calls[0][0], "gemini-3.1-flash-lite")

    async def test_grok_paths_trace_request(self) -> None:
        from kindly_web_search_mcp_server.search.grok import (
            grok_search,
            search_grok_openrouter,
        )
        from kindly_web_search_mcp_server.search import grok as grok_module

        provider_patcher, provider_span = self._span_patch(
            "kindly_web_search_mcp_server.search.grok.create_llm_operation_span"
        )

        class FakeProviderResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "choices": [
                        {
                            "message": {
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com",
                                        "title": "Example",
                                        "content": "Snippet",
                                    }
                                ]
                            }
                        }
                    ]
                }

        class FakeHttpClient:
            async def post(self, *args, **kwargs) -> FakeProviderResponse:
                return FakeProviderResponse()

        with provider_patcher as provider_create:
            with patch.object(grok_module.settings, "openrouter_api_key", "test-key"):
                results = await search_grok_openrouter(
                    "python tracing",
                    num_results=1,
                    http_client=FakeHttpClient(),
                )

        self.assertEqual(results[0].link, "https://example.com")
        self.assertEqual(provider_span.attributes["search.source_count"], 1)
        self.assertEqual(provider_create.call_args.kwargs["system"], "openrouter")
        self.assertEqual(
            provider_create.call_args.kwargs["attributes"][
                "search.num_results_requested"
            ],
            1,
        )

        tool_patcher, tool_span = self._span_patch(
            "kindly_web_search_mcp_server.search.grok.create_llm_operation_span"
        )

        class FakeChatResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "Result",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com",
                                        "title": "Example",
                                        "content": "Snippet",
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {
                        "server_tool_use": {"web_search_requests": 1},
                        "prompt_tokens": 20,
                        "completion_tokens": 11,
                        "total_tokens": 31,
                    },
                    "model": "x-ai/grok-4.3",
                }

        class FakeChatClient:
            async def __aenter__(self) -> "FakeChatClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def post(self, *args, **kwargs) -> FakeChatResponse:
                return FakeChatResponse()

        with (
            tool_patcher as tool_create,
            patch(
                "kindly_web_search_mcp_server.search.grok.httpx.AsyncClient",
                return_value=FakeChatClient(),
            ),
            patch.object(grok_module.settings, "openrouter_api_key", "test-key"),
        ):
            result = await grok_search(
                "python tracing",
                "find current docs",
                num_results=1,
            )

        self.assertEqual(result.citations[0]["url"], "https://example.com")
        self.assertEqual(tool_span.attributes["search.citation_count"], 1)
        self.assertEqual(tool_span.attributes["search.web_search_requests"], 1)
        self.assertEqual(tool_create.call_args.kwargs["system"], "openrouter")
        self.assertEqual(
            tool_create.call_args.kwargs["attributes"]["llm.model_name"],
            "x-ai/grok-4.3",
        )
        self.assertEqual(result.model_used, "x-ai/grok-4.3")
        self.assertEqual(result.input_tokens, 20)
        self.assertEqual(result.output_tokens, 11)

    async def test_gemini_search_with_grounding_traces_request(self) -> None:
        from kindly_web_search_mcp_server.search.gemini_search_tool import (
            gemini_search_with_grounding,
        )

        patcher, span = self._span_patch(
            "kindly_web_search_mcp_server.search.gemini_search_tool.create_llm_operation_span"
        )

        class FakePart:
            def __init__(self, text: str, thought: bool = False) -> None:
                self.text = text
                self.thought = thought

        class FakeCandidate:
            def __init__(self) -> None:
                self.content = SimpleNamespace(
                    parts=[FakePart("Answer body"), FakePart("Thinking", thought=True)]
                )
                self.grounding_metadata = SimpleNamespace(
                    web_search_queries=["python tracing"],
                    grounding_chunks=[
                        SimpleNamespace(
                            web=SimpleNamespace(
                                uri="https://example.com", title="Example"
                            )
                        )
                    ],
                    grounding_supports=[
                        SimpleNamespace(
                            segment=SimpleNamespace(
                                text="Answer body", start_index=0, end_index=11
                            ),
                            grounding_chunk_indices=[0],
                        )
                    ],
                    search_entry_point=SimpleNamespace(rendered_content="<div />"),
                )

        class FakeResponse:
            def __init__(self) -> None:
                self.candidates = [FakeCandidate()]
                self.parsed = None
                self.usage_metadata = SimpleNamespace(
                    prompt_token_count=15,
                    response_token_count=7,
                    total_token_count=22,
                )

        class FakeTypes:
            @staticmethod
            def Tool(*, google_search=None):
                return {"google_search": google_search}

            @staticmethod
            def GoogleSearch():
                return object()

            @staticmethod
            def GenerateContentConfig(**kwargs):
                return SimpleNamespace(**kwargs)

            @staticmethod
            def ThinkingConfig(**kwargs):
                return SimpleNamespace(**kwargs)

        class FakeModels:
            @staticmethod
            def generate_content(*, model, contents, config):
                return FakeResponse()

        class FakeClient:
            models = FakeModels()

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patcher as mock_create,
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.get_gemini_client",
                return_value=FakeClient(),
            ),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool._get_genai_types",
                return_value=FakeTypes(),
            ),
            patch(
                "kindly_web_search_mcp_server.search.gemini_search_tool.asyncio.to_thread",
                side_effect=fake_to_thread,
            ),
        ):
            result = await gemini_search_with_grounding(
                "python tracing",
                structured_output=False,
                research_goal="Find docs",
            )

        self.assertEqual(result.model_used, "gemini-3.1-flash-lite")
        self.assertEqual(result.input_tokens, 15)
        self.assertEqual(result.output_tokens, 7)
        self.assertEqual(span.attributes["search.grounding_chunk_count"], 1)
        self.assertEqual(span.attributes["search.model_used"], "gemini-3.1-flash-lite")
        self.assertEqual(mock_create.call_args.kwargs["system"], "google")
        self.assertEqual(
            mock_create.call_args.kwargs["attributes"]["search.fallback_tier_count"],
            3,
        )


if __name__ == "__main__":
    unittest.main()
