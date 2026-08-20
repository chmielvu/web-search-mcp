from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx

from kindly_web_search_mcp_server.cache.code_search import build_search_cache_key
from kindly_web_search_mcp_server.tools.code_search.huggingface import search_huggingface
from kindly_web_search_mcp_server.tools.code_search.models import (
    CodeSearchHit,
    CodeSearchRequest,
    CodeSearchResultType,
    ProviderResponse,
    Stats,
    to_public_result,
)
from kindly_web_search_mcp_server.tools.code_search.orchestrator import execute_code_search
from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
from kindly_web_search_mcp_server.tools.code_search.tool import _validate_request


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeClient(httpx.AsyncClient):
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class TestHuggingFaceValidation(IsolatedAsyncioTestCase):
    def test_mode_alias_and_filters_are_normalized(self):
        request = _validate_request(
            query="semantic search models",
            repositories=None,
            language=None,
            path=None,
            filename=None,
            extension=None,
            regexp=False,
            deep=False,
            repo_name=None,
            library_name=None,
            topic=None,
            mode="hf",
            huggingface_type="models",
            huggingface_sort_by="downloads",
            huggingface_min_param_count=1_000_000_000,
            huggingface_max_param_count=7_000_000_000,
        )
        self.assertEqual(request.mode, "huggingface")
        self.assertEqual(request.huggingface_type, "models")
        self.assertEqual(request.huggingface_sort_by, "downloads")

    def test_huggingface_filter_validation(self):
        with self.assertRaisesRegex(ValueError, "max_param_count"):
            _validate_request(
                query="semantic search models",
                repositories=None,
                language=None,
                path=None,
                filename=None,
                extension=None,
                regexp=False,
                deep=False,
                repo_name=None,
                library_name=None,
                topic=None,
                mode="huggingface",
                huggingface_min_param_count=10,
                huggingface_max_param_count=1,
            )


class TestHuggingFaceAdapter(IsolatedAsyncioTestCase):
    async def test_searches_both_asset_types_and_preserves_metadata(self):
        client = FakeClient(
            [
                FakeResponse(
                    {
                        "results": [
                            {
                                "dataset_id": "org/medical-data",
                                "similarity": 0.91,
                                "summary": "Medical question-answering data",
                                "likes": 4,
                                "downloads": 120,
                                "task": "question-answering",
                                "license": "apache-2.0",
                                "language": "en",
                                "last_modified": "2026-08-01T00:00:00Z",
                            }
                        ]
                    }
                ),
                FakeResponse(
                    {
                        "results": [
                            {
                                "model_id": "org/med-model",
                                "similarity": 0.88,
                                "summary": "Small medical language model",
                                "likes": 8,
                                "downloads": 900,
                                "param_count": 1_200_000_000,
                                "task": "text-generation",
                                "license": "mit",
                            }
                        ]
                    }
                ),
            ]
        )
        request = CodeSearchRequest(
            query="medical question answering",
            mode="huggingface",
            huggingface_type="both",
            max_results=10,
        )
        plan = build_query_plan(request.query, mode="huggingface")

        response = await search_huggingface(plan, request, http_client=client)

        self.assertEqual(response.provider, "huggingface")
        self.assertEqual(len(response.hits), 2)
        self.assertEqual(response.hits[0].repository, "org/medical-data")
        self.assertEqual(response.hits[0].source_metadata["asset_type"], "dataset")
        self.assertEqual(response.hits[1].source_metadata["param_count"], 1_200_000_000)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all(call[1]["params"]["k"] == 5 for call in client.calls))

    async def test_rejects_query_outside_api_bounds_without_http(self):
        client = FakeClient([])
        request = CodeSearchRequest(query="AI", mode="huggingface")
        plan = build_query_plan(request.query, mode="huggingface")

        response = await search_huggingface(plan, request, http_client=client)

        self.assertEqual(response.outcome, "error")
        self.assertEqual(response.diagnostics[0].failure_kind, "validation")
        self.assertEqual(client.calls, [])

    async def test_maps_rate_limit_to_partial_diagnostic(self):
        client = FakeClient([FakeResponse({}, status_code=429, headers={"retry-after": "2"})])
        request = CodeSearchRequest(
            query="semantic search models", mode="huggingface", huggingface_type="models"
        )
        plan = build_query_plan(request.query, mode="huggingface")

        response = await search_huggingface(plan, request, http_client=client)

        self.assertEqual(response.outcome, "partial")
        self.assertEqual(response.diagnostics[0].failure_kind, "rate_limit")
        self.assertEqual(response.diagnostics[0].retry_after_seconds, 2.0)


class TestHuggingFaceOrchestration(IsolatedAsyncioTestCase):
    async def test_huggingface_mode_is_exclusive(self):
        hf_response = ProviderResponse(
            provider="huggingface",
            hits=[
                CodeSearchHit(
                    provider="huggingface",
                    result_kind="repository",
                    repository="org/model",
                    url="https://huggingface.co/models/org/model",
                    snippet="A model card",
                    score=0.9,
                    source_metadata={
                        "asset_id": "org/model",
                        "asset_type": "model",
                        "semantic_score": 0.9,
                        "likes": 3,
                        "downloads": 10,
                    },
                )
            ],
        )
        request = CodeSearchRequest(query="semantic search models", mode="huggingface")
        plan = build_query_plan(request.query, mode="huggingface")

        with (
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_huggingface",
                AsyncMock(return_value=hf_response),
            ) as hf_search,
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_github",
                AsyncMock(side_effect=AssertionError("GitHub must not run in huggingface mode")),
            ),
        ):
            result = await execute_code_search(request, plan, http_client=FakeClient([]))

        hf_search.assert_awaited_once()
        self.assertEqual(result.outcome, "ok")
        self.assertEqual(result.results[0].provider, "huggingface")

    def test_public_projection_exposes_assets(self):
        hit = CodeSearchHit(
            provider="huggingface",
            result_kind="repository",
            repository="org/model",
            url="https://huggingface.co/models/org/model",
            snippet="A model card",
            source_metadata={
                "asset_id": "org/model",
                "asset_type": "model",
                "semantic_score": 0.87,
                "score_semantics": "provider_similarity",
                "likes": 4,
                "downloads": 100,
                "param_count": 1_000_000_000,
                "license": "apache-2.0",
            },
        )
        request = CodeSearchRequest(query="model", mode="huggingface")
        result = CodeSearchResultType(
            query=request.query,
            outcome="ok",
            results=[hit],
            repositories=[],
            diagnostics=[],
            stats=Stats(),
            query_metadata=build_query_plan(request.query, mode="huggingface").metadata,
        )

        public = to_public_result(result, plan=None)

        self.assertEqual(len(public.assets), 1)
        self.assertEqual(public.assets[0].asset_id, "org/model")
        self.assertEqual(public.assets[0].semantic_score, 0.87)
        self.assertEqual(public.assets[0].param_count, 1_000_000_000)

    def test_cache_key_includes_huggingface_filters(self):
        first = CodeSearchRequest(query="semantic search", mode="huggingface")
        second = CodeSearchRequest(
            query="semantic search",
            mode="huggingface",
            huggingface_type="models",
            huggingface_min_likes=10,
        )
        plan = build_query_plan("semantic search", mode="huggingface")

        self.assertNotEqual(
            build_search_cache_key(first, plan), build_search_cache_key(second, plan)
        )
