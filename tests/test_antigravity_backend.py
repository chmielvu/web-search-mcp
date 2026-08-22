"""Unit tests for the Antigravity managed-agent backend (gemini_search tool)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from kindly_web_search_mcp_server.search.antigravity_backend import (
    ANTIGRAVITY_AGENT_ID,
    call_antigravity_grounding,
)
from kindly_web_search_mcp_server.search.gemini_search_tool import (
    GeminiGroundingResult,
    gemini_search_with_grounding,
)
from kindly_web_search_mcp_server.settings import settings


def _completed_interaction_payload(
    *,
    answer_text: str = "Here is the summary.",
    queries: list[str] | None = None,
    fetched_urls: list[tuple[str, str]] | None = None,
    citations: list[dict[str, str]] | None = None,
    status: str = "completed",
    usage: dict[str, int] | None = None,
) -> dict:
    steps: list[dict] = []
    if queries:
        steps.append(
            {
                "type": "google_search_call",
                "arguments": {"queries": queries},
            }
        )
    if fetched_urls:
        steps.append(
            {
                "type": "url_context_result",
                "result": [{"url": u, "status": s} for u, s in fetched_urls],
            }
        )

    content_parts: list[dict] = [{"type": "text", "text": answer_text}]
    if citations:
        content_parts[0]["url_citations"] = citations

    steps.append({"type": "model_output", "content": content_parts})

    return {
        "id": "int_test_123",
        "status": status,
        "steps": steps,
        "usage": usage
        or {
            "total_input_tokens": 1200,
            "total_output_tokens": 350,
            "total_tokens": 1550,
        },
    }


class TestAntigravityBackend(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_mapping(self) -> None:
        get_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal get_calls
            if request.method == "POST" and str(request.url).endswith("/interactions"):
                body = json.loads(request.content.decode())
                self.assertEqual(body["agent"], ANTIGRAVITY_AGENT_ID)
                self.assertTrue(body["background"])
                return httpx.Response(200, json={"id": "int_test_123", "status": "in_progress"})
            if request.method == "GET" and "int_test_123" in str(request.url):
                get_calls += 1
                if get_calls == 1:
                    return httpx.Response(200, json={"id": "int_test_123", "status": "in_progress"})
                return httpx.Response(
                    200,
                    json=_completed_interaction_payload(
                        answer_text="Sample answer text.",
                        queries=["sample query 1", "sample query 2"],
                        fetched_urls=[
                            ("https://example.com/a", "success"),
                            ("https://example.com/bad", "error"),
                        ],
                        citations=[
                            {"url": "https://example.com/a", "title": "Example A Title"}
                        ],
                    ),
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        with patch.object(settings, "gemini_api_key", "test_key"), patch.object(
            settings, "antigravity_poll_interval_seconds", 0.001
        ):
            result = await call_antigravity_grounding(
                "test query",
                system_prompt="system instructions",
                structured_output=False,
                transport=transport,
            )

        self.assertEqual(result.mode, "single")
        self.assertEqual(result.answer, "Sample answer text.")
        self.assertEqual(result.search_queries, ["sample query 1", "sample query 2"])
        # Only success-status URLs become sources
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0]["url"], "https://example.com/a")
        self.assertEqual(result.sources[0]["title"], "Example A Title")
        self.assertEqual(len(result.url_citations), 1)
        self.assertEqual(result.prompt_tokens, 1200)
        self.assertEqual(result.completion_tokens, 350)
        self.assertEqual(result.total_tokens, 1550)
        self.assertTrue(result.model_used.startswith("antigravity/"))
        self.assertIsNone(result.fallback_reason)

    async def test_structured_output_json_fence_stripping(self) -> None:
        structured_body = {
            "executive_summary": "Summary here",
            "key_findings": ["Finding 1 [1]"],
            "sources": [{"url": "https://example.com/doc", "title": "Doc Title"}],
            "confidence": "high",
            "uncertainties": None,
        }
        fenced_text = f"```json\n{json.dumps(structured_body)}\n```"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "int_struct", "status": "in_progress"})
            return httpx.Response(
                200,
                json=_completed_interaction_payload(answer_text=fenced_text),
            )

        transport = httpx.MockTransport(handler)

        with patch.object(settings, "gemini_api_key", "test_key"), patch.object(
            settings, "antigravity_poll_interval_seconds", 0.001
        ):
            result = await call_antigravity_grounding(
                "query",
                structured_output=True,
                transport=transport,
            )

        self.assertIsNotNone(result.structured_data)
        assert result.structured_data is not None
        self.assertEqual(result.structured_data["executive_summary"], "Summary here")
        self.assertEqual(result.structured_data["confidence"], "high")

    async def test_incomplete_budget_status_annotates_fallback_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "int_budget", "status": "in_progress"})
            return httpx.Response(
                200,
                json=_completed_interaction_payload(
                    answer_text="Partial answer",
                    status="incomplete",
                ),
            )

        transport = httpx.MockTransport(handler)

        with patch.object(settings, "gemini_api_key", "test_key"), patch.object(
            settings, "antigravity_poll_interval_seconds", 0.001
        ):
            result = await call_antigravity_grounding("query", transport=transport)

        self.assertEqual(result.answer, "Partial answer")
        self.assertEqual(result.fallback_reason, "antigravity_incomplete")

    async def test_failure_status_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "int_fail", "status": "in_progress"})
            return httpx.Response(
                200,
                json={"id": "int_fail", "status": "failed", "error": {"message": "quota exceeded"}},
            )

        transport = httpx.MockTransport(handler)

        with patch.object(settings, "gemini_api_key", "test_key"), patch.object(
            settings, "antigravity_poll_interval_seconds", 0.001
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await call_antigravity_grounding("query", transport=transport)
        self.assertIn("failed", str(ctx.exception).lower())

    async def test_timeout_cancels_and_raises(self) -> None:
        cancelled = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal cancelled
            if request.method == "POST" and "cancel" in str(request.url):
                cancelled = True
                return httpx.Response(200, json={})
            if request.method == "POST":
                return httpx.Response(200, json={"id": "int_to", "status": "in_progress"})
            return httpx.Response(200, json={"id": "int_to", "status": "in_progress"})

        transport = httpx.MockTransport(handler)

        with patch.object(settings, "gemini_api_key", "test_key"), patch.object(
            settings, "antigravity_timeout_seconds", 0.05
        ), patch.object(settings, "antigravity_poll_interval_seconds", 0.01):
            with self.assertRaises(RuntimeError) as ctx:
                await call_antigravity_grounding("query", transport=transport)

        self.assertTrue(cancelled)
        self.assertIn("timed out", str(ctx.exception).lower())

    async def test_missing_api_key_raises(self) -> None:
        with patch.object(settings, "gemini_api_key", ""), patch.object(
            settings, "gemini_second_api_key", ""
        ), patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_SECOND_API_KEY": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                await call_antigravity_grounding("query")
        self.assertIn("api key", str(ctx.exception).lower())


class TestWiring(unittest.IsolatedAsyncioTestCase):
    async def test_antigravity_backend_selected_and_falls_back_on_error(self) -> None:
        grounding_sentinel = GeminiGroundingResult(query="q", answer="grounding answer")

        with patch.object(settings, "gemini_search_backend", "antigravity"), patch(
            "kindly_web_search_mcp_server.search.antigravity_backend.call_antigravity_grounding",
            new=AsyncMock(side_effect=RuntimeError("simulated antigravity failure")),
        ), patch(
            "kindly_web_search_mcp_server.search.gemini_search_tool._call_single_grounding",
            new=AsyncMock(return_value=grounding_sentinel),
        ):
            result = await gemini_search_with_grounding("q", parallel_mode=False)

        self.assertIs(result, grounding_sentinel)

    async def test_antigravity_backend_success_returns_directly(self) -> None:
        antigravity_sentinel = GeminiGroundingResult(
            query="q",
            mode="single",
            answer="antigravity direct answer",
            model_used="antigravity/gemini-3.7-flash",
        )

        with patch.object(settings, "gemini_search_backend", "antigravity"), patch(
            "kindly_web_search_mcp_server.search.antigravity_backend.call_antigravity_grounding",
            new=AsyncMock(return_value=antigravity_sentinel),
        ), patch(
            "kindly_web_search_mcp_server.search.gemini_search_tool._call_single_grounding",
            new=AsyncMock(),
        ) as mock_single:
            result = await gemini_search_with_grounding("q", parallel_mode=True)
            mock_single.assert_not_called()

        self.assertEqual(result.answer, "antigravity direct answer")
        self.assertEqual(result.mode, "single")


if __name__ == "__main__":
    unittest.main()
