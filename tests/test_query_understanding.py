from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult
from kindly_web_search_mcp_server.search.understanding.resolver import resolve_query_understanding
from kindly_web_search_mcp_server.settings import settings


class TestQueryUnderstanding(IsolatedAsyncioTestCase):
    def test_query_understanding_result_preserves_terms(self) -> None:
        result = QueryUnderstandingResult(
            intent="comparison",
            confidence=0.88,
            entities=[
                EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)
            ],
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
