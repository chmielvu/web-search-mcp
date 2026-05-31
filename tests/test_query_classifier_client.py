from __future__ import annotations

import asyncio
from unittest.mock import patch


def _make_client():
    from kindly_web_search_mcp_server.search.query_classifier_client import (
        FunctionGemmaClient,
    )

    return FunctionGemmaClient(
        base_url="https://functiongemma-classifier.example.test",
        enabled=True,
        timeout_seconds=5,
        max_tokens=128,
    )


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self) -> "_FakeHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append((url, json))
        return _FakeResponse(self.payload)


def test_classify_query_parses_generate_response() -> None:
    from kindly_web_search_mcp_server.search.query_classifier_client import (
        ClassifierOutput,
    )

    client = _make_client()
    http_client = _FakeHttpClient(
        {
            "result": {
                "intent": "comparison",
                "should_decompose": True,
                "confidence": 0.97,
                "routing": {"keyword": True, "neural": True, "community": True},
            },
            "latency_ms": 12,
            "tokens_generated": 42,
        }
    )

    with patch(
        "kindly_web_search_mcp_server.search.query_classifier_client.httpx.Client",
        return_value=http_client,
    ):
        result = asyncio.run(
            client.classify_query(
                "React 19 vs Vue 4 SSR performance",
                research_goal="compare React and Vue developer experience",
            )
        )

    assert isinstance(result, ClassifierOutput)
    assert result.intent == "comparison"
    assert result.should_decompose is True
    assert result.routing.community is True
    assert len(http_client.calls) == 1
    assert http_client.calls[0][0].endswith("/generate")


def test_classify_query_uses_cache_key_for_research_goal() -> None:
    client = _make_client()
    http_client = _FakeHttpClient(
        {
            "result": {
                "intent": "code",
                "should_decompose": False,
                "confidence": 0.88,
                "routing": {"keyword": True, "neural": False, "community": False},
            },
            "latency_ms": 10,
            "tokens_generated": 12,
        }
    )

    with patch(
        "kindly_web_search_mcp_server.search.query_classifier_client.httpx.Client",
        return_value=http_client,
    ):
        asyncio.run(client.classify_query("FastMCP docs", research_goal="goal one"))
        asyncio.run(client.classify_query("FastMCP docs", research_goal="goal two"))

    assert len(http_client.calls) == 2


def test_classify_query_falls_back_when_disabled() -> None:
    from kindly_web_search_mcp_server.search.query_classifier_client import (
        ClassifierOutput,
    )

    client = _make_client()
    client.enabled = False

    result = asyncio.run(client.classify_query("github issue docker"))

    assert isinstance(result, ClassifierOutput)
    assert result.intent in {"code", "general_research", "comparison"}
    assert result.routing.keyword is True


def test_circuit_breaker_opens_after_repeated_failures() -> None:
    client = _make_client()

    class _FailingHttpClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, url: str, json: dict) -> None:
            raise RuntimeError("boom")

    failing_client = _FailingHttpClient()
    with patch(
        "kindly_web_search_mcp_server.search.query_classifier_client.httpx.Client",
        return_value=failing_client,
    ):
        asyncio.run(client.classify_query("one"))
        asyncio.run(client.classify_query("two"))
        asyncio.run(client.classify_query("three"))
        asyncio.run(client.classify_query("four"))

    assert "open" in str(client._breaker.current_state).lower()
