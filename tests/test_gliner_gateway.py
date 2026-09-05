"""Contract tests for the hosted VPS GLiNER2 query gateway."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from kindly_web_search_mcp_server.entity.default_schema import DEFAULT_QUERY_LABELS
from kindly_web_search_mcp_server.entity.gliner_client import GLiNER2Client
from kindly_web_search_mcp_server.settings import settings


class _Response:
    def __init__(self, payload, status_code: int = 200, *, json_error: Exception | None = None):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://classifier.test/v2/query-understanding")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("service failure", request=request, response=response)

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class _AsyncClient:
    response: _Response | None = None
    error: Exception | None = None
    calls: list[tuple[str, dict]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url: str, *, json: dict):
        self.calls.append((url, json))
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


@contextmanager
def _run_client(response: _Response | None = None, error: Exception | None = None):
    _AsyncClient.response = response
    _AsyncClient.error = error
    _AsyncClient.calls = []
    with patch("kindly_web_search_mcp_server.entity.gliner_client.httpx.AsyncClient", _AsyncClient):
        yield _AsyncClient.calls


def _payload(*, confidence: float = 0.91) -> dict:
    return {
        "intent": "comparison",
        "confidence": confidence,
        "entities": {
            "package": [
                {"text": "FastAPI", "start": 8, "end": 15, "confidence": 0.96},
                {"text": "Starlette", "start": 27, "end": 36, "confidence": 0.94},
            ],
            "version": [{"text": "0.100", "start": 16, "end": 21, "confidence": 0.99}],
        },
        "relations": {
            "compares_with": [
                {
                    "head": {
                        "text": "FastAPI",
                        "label": "package",
                        "start": 8,
                        "end": 15,
                        "confidence": 0.96,
                    },
                    "tail": {
                        "text": "Starlette",
                        "label": "package",
                        "start": 27,
                        "end": 36,
                        "confidence": 0.94,
                    },
                }
            ]
        },
        "model_version": "fastino/gliner2-multi-v1",
        "latency_ms": 123.4,
    }


def _classify_ner_post(*, intent: str = "comparison", confidence: float = 0.91):
    async def post(self, path, payload, *, operation, timeout=None):
        del self, operation, timeout
        if path == "/classify":
            return (
                {
                    "intent": intent,
                    "scores": [{"label": intent, "score": confidence}],
                },
                12.0,
            )
        if path == "/ner":
            return (
                {
                    "entities": [
                        {
                            "text": "FastAPI",
                            "label": "package",
                            "start": 8,
                            "end": 15,
                            "score": 0.96,
                        },
                        {
                            "text": "Starlette",
                            "label": "package",
                            "start": 27,
                            "end": 36,
                            "score": 0.94,
                        },
                    ]
                },
                40.0,
            )
        raise AssertionError(path)

    return post


@pytest.mark.asyncio
async def test_query_gateway_calls_classify_and_ner(monkeypatch):
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)
    posts: list[tuple[str, dict]] = []

    async def post(self, path, payload, *, operation, timeout=None):
        del self, operation, timeout
        posts.append((path, payload))
        if path == "/classify":
            return (
                {"intent": "comparison", "scores": [{"label": "comparison", "score": 0.91}]},
                12.0,
            )
        if path == "/ner":
            return (
                {
                    "entities": [
                        {
                            "text": "FastAPI",
                            "label": "package",
                            "start": 8,
                            "end": 15,
                            "score": 0.96,
                        },
                        {
                            "text": "Starlette",
                            "label": "package",
                            "start": 27,
                            "end": 36,
                            "score": 0.94,
                        },
                    ]
                },
                40.0,
            )
        raise AssertionError(path)

    monkeypatch.setattr(GLiNER2Client, "_post", post)
    analysis = await GLiNER2Client(base_url="http://127.0.0.1:8000").analyze_query(
        "Compare FastAPI 0.100 with Starlette"
    )

    assert analysis.fallback is False
    assert analysis.understanding.intent == "comparison"
    assert analysis.understanding.preserved_terms == ["FastAPI", "Starlette"]
    assert {path for path, _ in posts} == {"/classify", "/ner"}
    ner_payload = next(payload for path, payload in posts if path == "/ner")
    assert ner_payload["labels"] == list(DEFAULT_QUERY_LABELS)
    assert all(path != "/v2/query-understanding" for path, _ in posts)


@pytest.mark.asyncio
async def test_query_gateway_enforces_low_confidence_general_fallback(monkeypatch):
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)
    monkeypatch.setattr(GLiNER2Client, "_post", _classify_ner_post(confidence=0.2))
    analysis = await GLiNER2Client(base_url="http://127.0.0.1:8000").analyze_query(
        "Compare FastAPI 0.100 with Starlette"
    )

    assert analysis.understanding.intent == "general"
    assert analysis.understanding.confidence == 0.2
    assert analysis.understanding.rationale == "gliner2-low-confidence"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, response",
    [
        (httpx.TimeoutException("timed out"), None),
        (httpx.ConnectError("unreachable"), None),
        (None, _Response({}, status_code=503)),
        (None, _Response({}, json_error=ValueError("not json"))),
    ],
)
async def test_query_gateway_failures_are_deterministic_fallbacks(monkeypatch, error, response):
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)
    with _run_client(response, error):
        analysis = await GLiNER2Client(base_url="http://127.0.0.1:8000").analyze_query("a query")

    assert analysis.fallback is True
    assert analysis.understanding.intent == "general"
    assert analysis.understanding.entities == []
    assert analysis.understanding.relations == []
    assert analysis.error_reason


@pytest.mark.asyncio
async def test_query_gateway_disabled_does_not_make_http_call(monkeypatch):
    monkeypatch.setattr(settings, "intent_classifier_enabled", False)
    with _run_client(_Response(_payload())) as calls:
        analysis = await GLiNER2Client(base_url="http://127.0.0.1:8000").analyze_query("a query")

    assert analysis.fallback is True
    assert analysis.error_reason == "gliner2-disabled"
    assert calls == []


@pytest.mark.asyncio
async def test_query_gateway_preserves_model_and_latency_metadata(monkeypatch):
    monkeypatch.setattr(settings, "intent_classifier_enabled", True)
    monkeypatch.setattr(GLiNER2Client, "_post", _classify_ner_post())
    analysis = await GLiNER2Client(base_url="http://127.0.0.1:8000").analyze_query(
        "Compare FastAPI 0.100 with Starlette"
    )

    assert analysis.model_version.endswith("+unifiedml-composed")
    assert analysis.latency_ms >= 0.0
    assert analysis.understanding.preserved_terms == ["FastAPI", "Starlette"]
