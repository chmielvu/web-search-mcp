from __future__ import annotations

import pytest
from dataclasses import replace
from typing import Any, cast

from kindly_web_search_mcp_server.entity.gliner_client import QueryFeatureAnalysis
from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchRequest
from kindly_web_search_mcp_server.tools.code_search.optimization import optimize_query_plan
from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
from kindly_web_search_mcp_server.cache.code_search import build_search_cache_key


class _FakeGliner:
    def __init__(self, features: QueryFeatureAnalysis) -> None:
        self.features = features

    async def analyze_query_features(self, _query: str) -> QueryFeatureAnalysis:
        return self.features


def _features(*entities: EntitySpan) -> QueryFeatureAnalysis:
    return QueryFeatureAnalysis(
        intent="implementation",
        confidence=0.95,
        entities=tuple(entities),
        model_version="test-gliner",
        latency_ms=1.0,
    )


@pytest.mark.asyncio
async def test_package_entity_enables_context7_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    from kindly_web_search_mcp_server.tools.code_search import optimization

    monkeypatch.setattr(
        optimization,
        "get_gliner_client",
        lambda: _FakeGliner(
            _features(EntitySpan(text="FastAPI", label="package", confidence=0.96))
        ),
    )
    request = CodeSearchRequest(query="FastAPI authentication", mode="docs")
    plan = build_query_plan(request.query, mode=request.mode)
    enriched = await optimize_query_plan(plan, request)

    assert enriched.library_hint == "FastAPI"
    assert enriched.repository_hint is None
    assert enriched.resolution_source == "gliner2"
    assert enriched.metadata.resolution_hints == {
        "library": "FastAPI",
        "source": "gliner2",
    }


@pytest.mark.asyncio
async def test_repository_entity_is_normalized_and_confidence_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kindly_web_search_mcp_server.tools.code_search import optimization

    monkeypatch.setattr(
        optimization,
        "get_gliner_client",
        lambda: _FakeGliner(
            _features(
                EntitySpan(text="fastapi/fastapi", label="repo_ref", confidence=0.95),
                EntitySpan(text="uncertain-lib", label="package", confidence=0.30),
            )
        ),
    )
    request = CodeSearchRequest(query="fastapi/fastapi auth", mode="docs")
    plan = build_query_plan(request.query, mode=request.mode)
    enriched = await optimize_query_plan(plan, request)

    assert enriched.repository_hint == "fastapi/fastapi"
    assert enriched.library_hint is None


@pytest.mark.asyncio
async def test_resolution_hint_changes_search_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kindly_web_search_mcp_server.tools.code_search import optimization

    monkeypatch.setattr(
        optimization,
        "get_gliner_client",
        lambda: _FakeGliner(
            _features(EntitySpan(text="FastAPI", label="package", confidence=0.96))
        ),
    )
    request = CodeSearchRequest(query="FastAPI authentication", mode="docs")
    plan = build_query_plan(request.query, mode=request.mode)
    enriched = await optimize_query_plan(plan, request)

    assert build_search_cache_key(request, plan) != build_search_cache_key(request, enriched)


@pytest.mark.asyncio
async def test_docs_dispatch_uses_inferred_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    from kindly_web_search_mcp_server.tools.code_search import docs
    from kindly_web_search_mcp_server.tools.code_search.models import ProviderResponse

    async def fake_context7(*_args: Any, **_kwargs: Any) -> ProviderResponse:
        return ProviderResponse(provider="context7")

    monkeypatch.setattr(docs, "search_context7", fake_context7)
    request = CodeSearchRequest(query="FastAPI authentication")
    plan = replace(build_query_plan(request.query), library_hint="FastAPI")
    responses = await docs.search_docs(plan, request, http_client=cast(Any, object()))
    assert [response.provider for response in responses] == ["context7"]
