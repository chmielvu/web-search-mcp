from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult
from kindly_web_search_mcp_server.search.understanding.resolver import resolve_query_understanding
from kindly_web_search_mcp_server.settings import settings


class TestQueryUnderstanding(IsolatedAsyncioTestCase):
    def test_query_understanding_result_preserves_terms(self) -> None:
        result = QueryUnderstandingResult(
            intent="comparison",
            confidence=0.88,
            entities=[EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)],
            preserved_terms=["FastAPI", "Pydantic"],
            compared_entities=["FastAPI", "Starlette"],
            rationale="Compare frameworks.",
        )

        self.assertEqual(result.schema_version, "0.2")
        self.assertEqual(result.must_keep_terms, ["FastAPI", "Pydantic"])
        self.assertEqual(result.compared_entities, ["FastAPI", "Starlette"])

    async def test_query_understanding_falls_back_to_general_when_llm_fails(self) -> None:
        class _BrokenWorker:
            async def complete_structured(self, request) -> None:  # noqa: ANN001
                raise RuntimeError("boom")

        with (
            patch.object(settings, "query_understanding_jsonl_enabled", False),
            patch(  # ONNX classifier returns None → forces LLM fallback
                "kindly_web_search_mcp_server.search.understanding.onnx_classifier.classify_intent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "kindly_web_search_mcp_server.search.understanding.resolver.build_llm_worker",
                return_value=_BrokenWorker(),
            ),
        ):
            result = await resolve_query_understanding(
                query="FastAPI docs vs Starlette docs",
                research_goal=None,
                intent_hint=None,
                session_id=None,
            )

        self.assertEqual(result.intent, "general")
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.should_decompose)

    async def test_query_understanding_forwards_langfuse_context(self) -> None:
        class _Worker:
            def __init__(self) -> None:
                self.complete_structured = AsyncMock(
                    return_value=SimpleNamespace(
                        model_name="groq/openai/gpt-oss-20b",
                        endpoint_name="groq",
                        content=(
                            '{"intent":"general","confidence":0.9,'
                            '"should_decompose":false,"rationale":"ok",'
                            '"entities":[],"must_keep_terms":[],"preserved_terms":[]}'
                        ),
                    )
                )

        worker = _Worker()
        with (
            patch.object(settings, "query_understanding_jsonl_enabled", False),
            patch.object(settings, "analytics_enabled", False),
            patch(  # ONNX classifier returns None → forces LLM fallback
                "kindly_web_search_mcp_server.search.understanding.onnx_classifier.classify_intent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "kindly_web_search_mcp_server.search.understanding.resolver.get_ab_overrides",
                return_value=None,
            ),
            patch(
                "kindly_web_search_mcp_server.search.understanding.resolver.build_llm_worker",
                return_value=worker,
            ),
        ):
            result = await resolve_query_understanding(
                query="FastAPI docs",
                research_goal="Locate official docs",
                intent_hint=None,
                session_id="session-123",
                run_key="run-456",
            )

        self.assertEqual(result.intent, "general")
        request = worker.complete_structured.await_args.args[0]
        self.assertIsNotNone(request.langfuse)
        self.assertEqual(request.langfuse.trace_name, "query_understanding")
        self.assertEqual(request.langfuse.session_id, "session-123")
        self.assertEqual(request.langfuse.metadata["task"], "query_understanding")
        self.assertEqual(request.langfuse.metadata["run_key"], "run-456")
