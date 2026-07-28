from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from kindly_web_search_mcp_server.entity.models import EntityRelation, EntitySpan
from kindly_web_search_mcp_server.entity.gliner_client import GatewayAnalysis
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult
from kindly_web_search_mcp_server.search.understanding.resolver import resolve_query_understanding
from kindly_web_search_mcp_server.settings import settings


class TestQueryUnderstanding(IsolatedAsyncioTestCase):
    async def test_resolver_makes_one_combined_vps_call_and_derives_fields(self) -> None:
        query = "Compare FastAPI with Starlette in Python"
        fastapi_start = query.index("FastAPI")
        starlette_start = query.index("Starlette")
        fastapi = EntitySpan(
            text="FastAPI",
            label="package",
            start=fastapi_start,
            end=fastapi_start + 7,
            confidence=0.96,
        )
        starlette = EntitySpan(
            text="Starlette",
            label="package",
            start=starlette_start,
            end=starlette_start + 9,
            confidence=0.94,
        )
        understanding = QueryUnderstandingResult(
            intent="comparison",
            confidence=0.91,
            entities=[fastapi, starlette],
            relations=[
                EntityRelation(
                    relation="compares_with",
                    head=fastapi,
                    tail=starlette,
                    confidence=0.94,
                )
            ],
            preserved_terms=["FastAPI", "Starlette"],
            compared_entities=["FastAPI", "Starlette"],
            domain_hints=["Python"],
            rationale="gliner2-combined",
            should_decompose=True,
        )
        client = SimpleNamespace(
            base_url="http://127.0.0.1:8000",
            analyze_query=AsyncMock(
                return_value=GatewayAnalysis(
                    understanding=understanding,
                    model_version="fastino/gliner2-multi-v1",
                    latency_ms=123.4,
                )
            ),
        )
        with (
            patch.object(settings, "query_understanding_jsonl_enabled", False),
            patch(
                "kindly_web_search_mcp_server.search.understanding.resolver.get_gliner_client",
                return_value=client,
            ),
        ):
            result = await resolve_query_understanding(
                query=query,
                research_goal="Compare frameworks",
                intent_hint=None,
                session_id=None,
                run_key="run-123",
            )

        self.assertEqual(result.intent, "comparison")
        self.assertEqual(result.relations[0].relation, "compares_with")
        self.assertEqual(result.compared_entities, ["FastAPI", "Starlette"])
        self.assertTrue(result.should_decompose)
        client.analyze_query.assert_awaited_once_with(query)

    async def test_service_failure_returns_general_without_llm_fallback(self) -> None:
        fallback = QueryUnderstandingResult(
            intent="general",
            confidence=0.0,
            rationale="gliner2-unavailable",
        )
        client = SimpleNamespace(
            base_url="http://127.0.0.1:8000",
            analyze_query=AsyncMock(
                return_value=GatewayAnalysis(
                    understanding=fallback,
                    model_version="fastino/gliner2-multi-v1",
                    latency_ms=2.0,
                    fallback=True,
                    error_reason="gliner2-unavailable",
                )
            ),
        )
        with (
            patch.object(settings, "query_understanding_jsonl_enabled", False),
            patch(
                "kindly_web_search_mcp_server.search.understanding.resolver.get_gliner_client",
                return_value=client,
            ),
        ):
            result = await resolve_query_understanding(
                query="FastAPI docs",
                research_goal=None,
                intent_hint=None,
            )

        self.assertEqual(result.intent, "general")
        self.assertEqual(result.rationale, "gliner2-unavailable")
        self.assertEqual(result.entities, [])
        self.assertEqual(result.relations, [])
        client.analyze_query.assert_awaited_once()

    async def test_result_relations_default_empty_for_legacy_callers(self) -> None:
        result = QueryUnderstandingResult(
            intent="general",
            confidence=0.4,
            preserved_terms=["FastAPI"],
            rationale="legacy-fixture",
        )
        self.assertEqual(result.relations, [])
        self.assertEqual(result.schema_version, "0.3")
