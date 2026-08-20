"""Deterministic unit tests for bounded Sourcegraph retries and stream -> GraphQL fallback."""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest
from kindly_web_search_mcp_server.tools.code_search.models import (
    CodeSearchRequest,
    SearchBudget,
)
from kindly_web_search_mcp_server.tools.code_search.query import QueryPlan, build_query_plan
from kindly_web_search_mcp_server.tools.code_search.sourcegraph import (
    _graphql_search_variant,
    _parse_retry_after_header,
    _resolve_max_retries,
    _stream_search_variant,
    search_sourcegraph,
)

_SAMPLE_STREAM_MATCHES_EVENT = (
    "event: matches\n"
    'data: [{"type": "content", "repository": "github.com/owner/repo", '
    '"path": "src/retry.py", "lineMatches": [{"lineNumber": 42, "preview": "retry_logic = True"}]}]\n\n'
    "event: done\ndata: {}\n\n"
)

_SAMPLE_GRAPHQL_PAYLOAD = {
    "data": {
        "search": {
            "results": {
                "matchCount": 1,
                "limitHit": False,
                "results": [
                    {
                        "__typename": "FileMatch",
                        "file": {
                            "path": "src/retry.py",
                            "url": "/github.com/owner/repo/-/blob/src/retry.py",
                        },
                        "repository": {"name": "github.com/owner/repo", "url": "https://github.com/owner/repo"},
                        "lineMatches": [{"lineNumber": 42, "preview": "retry_logic = True"}],
                        "symbols": [],
                    }
                ],
            }
        }
    }
}


def _make_request_and_plan(query: str = "retry_logic") -> tuple[QueryPlan, CodeSearchRequest]:
    plan = build_query_plan(query, repositories=["owner/repo"])
    request = CodeSearchRequest(
        query=query,
        repositories=("owner/repo",),
        max_results=10,
        budget=SearchBudget(max_query_variants=1),
    )
    return plan, request


@pytest.fixture(autouse=True)
def _fast_asyncio_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all retry backoff sleeps are instantaneous in unit tests."""

    async def _instant_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _instant_sleep)
# 1. 429 Retry-After then success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_retry_429_with_retry_after_then_success() -> None:
    """Stream transport receives 429 with Retry-After, retries, and succeeds on 2nd attempt."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.01"},
                text="Rate limited",
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=_SAMPLE_STREAM_MATCHES_EVENT,
        )

    plan, request = _make_request_and_plan()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await search_sourcegraph(plan, request, http_client=client)

    assert len(calls) == 2
    assert response.provider == "sourcegraph"
    assert len(response.hits) == 1
    assert response.hits[0].repository == "owner/repo"
    assert response.hits[0].path == "src/retry.py"
    assert response.metadata["transports"] == ["stream"]
    assert response.metadata["transport_summary"]["stream"] == 1


@pytest.mark.asyncio
async def test_graphql_retry_429_with_retry_after_then_success() -> None:
    """GraphQL transport receives 429 with Retry-After, retries, and succeeds on 2nd attempt."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.01"},
                text="Rate limited",
            )
        return httpx.Response(200, json=_SAMPLE_GRAPHQL_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="repo:^github\\.com/owner/repo$ retry_logic",
            var_name="retry_logic",
            var_kind="lexical",
            max_results=10,
        )

    assert len(calls) == 2
    assert len(hits) == 1
    assert hits[0].repository == "owner/repo"
    assert transport_name == "graphql_fallback"


# ---------------------------------------------------------------------------
# 2. Transient 503 then success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_transient_503_then_success() -> None:
    """Stream transport retries transient 503 with exponential backoff and succeeds on 2nd attempt."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=_SAMPLE_STREAM_MATCHES_EVENT,
        )

    plan, request = _make_request_and_plan()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await search_sourcegraph(plan, request, http_client=client)

    assert len(calls) == 2
    assert len(response.hits) == 1
    assert response.hits[0].repository == "owner/repo"
    assert response.metadata["transports"] == ["stream"]


@pytest.mark.asyncio
async def test_graphql_transient_503_then_success() -> None:
    """GraphQL transport retries transient 503 and succeeds on 2nd attempt."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json=_SAMPLE_GRAPHQL_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="repo:^github\\.com/owner/repo$ retry_logic",
            var_name="retry_logic",
            var_kind="lexical",
            max_results=10,
        )

    assert len(calls) == 2
    assert len(hits) == 1
    assert hits[0].repository == "owner/repo"
    assert transport_name == "graphql_fallback"


# ---------------------------------------------------------------------------
# 3. Transport failure then fallback/success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_failure_then_fallback_success() -> None:
    """Stream transport encounters network connection errors, exhausts retries, and falls back to GraphQL successfully."""
    stream_calls: list[httpx.Request] = []
    graphql_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if ".api/search/stream" in str(request.url):
            stream_calls.append(request)
            raise httpx.ConnectError("Connection refused by peer", request=request)
        if ".api/graphql" in str(request.url):
            graphql_calls.append(request)
            return httpx.Response(200, json=_SAMPLE_GRAPHQL_PAYLOAD)
        raise AssertionError(f"Unexpected request: {request.url}")

    plan, request = _make_request_and_plan()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await search_sourcegraph(plan, request, http_client=client)

    # Stream attempted initial + 1 retry = 2 calls
    assert len(stream_calls) == 2
    # GraphQL fallback executed 1 call and succeeded
    assert len(graphql_calls) == 1
    assert len(response.hits) == 1
    assert response.hits[0].repository == "owner/repo"
    assert response.metadata["transports"] == ["graphql_fallback"]
    assert response.metadata["transport_summary"]["graphql_fallback"] == 1
    assert response.metadata["transport_summary"]["stream"] == 0


@pytest.mark.asyncio
async def test_stream_transport_error_then_stream_success() -> None:
    """Stream transport encounters connection error on attempt 1, retries, and succeeds on attempt 2 (exactly 2 GETs)."""
    stream_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stream_calls.append(request)
        if len(stream_calls) == 1:
            raise httpx.ConnectError("Connection dropped", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=_SAMPLE_STREAM_MATCHES_EVENT,
        )

    plan, request = _make_request_and_plan()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await search_sourcegraph(plan, request, http_client=client)

    assert len(stream_calls) == 2
    assert len(response.hits) == 1
    assert response.hits[0].repository == "owner/repo"
    assert response.metadata["transports"] == ["stream"]
    assert response.metadata["transport_summary"]["stream"] == 1
# ---------------------------------------------------------------------------
# 4. Non-retryable 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_retryable_400_not_retried() -> None:
    """Non-retryable 400 Bad Request is not retried by stream or GraphQL."""
    stream_calls: list[httpx.Request] = []
    graphql_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if ".api/search/stream" in str(request.url):
            stream_calls.append(request)
            return httpx.Response(400, text="Bad Request: invalid query syntax")
        if ".api/graphql" in str(request.url):
            graphql_calls.append(request)
            return httpx.Response(400, text="Bad Request: invalid query syntax")
        raise AssertionError(f"Unexpected request: {request.url}")

    plan, request = _make_request_and_plan()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await search_sourcegraph(plan, request, http_client=client)

    # Stream does not retry 400, falls back to GraphQL once, GraphQL does not retry 400
    assert len(stream_calls) == 1
    assert len(graphql_calls) == 1
    assert len(response.hits) == 0
    assert len(response.diagnostics) == 1
    assert response.diagnostics[0].status_code == 400
    assert "400" in response.diagnostics[0].message
    assert response.diagnostics[0].outcome == "error"


@pytest.mark.asyncio
async def test_ordinary_403_not_retried() -> None:
    """403 Forbidden is not retried."""
    graphql_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        graphql_calls.append(request)
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="repo:^github\\.com/owner/repo$ retry_logic",
            var_name="retry_logic",
            var_kind="lexical",
            max_results=10,
        )

    assert len(graphql_calls) == 1
    assert hits == []
    assert len(diags) == 1
    assert diags[0].status_code == 403


# ---------------------------------------------------------------------------
# 5. No retry after deadline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_retry_after_deadline_expired() -> None:
    """When budget deadline is in the past, no retries are attempted on transient errors."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, text="Service Unavailable")

    # Set deadline to now - 1.0 (already expired)
    past_deadline = time.monotonic() - 1.0
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _stream_search_variant(
            client,
            headers={},
            query_variant="repo:^github\\.com/owner/repo$ retry_logic",
            var_name="retry_logic",
            var_kind="lexical",
            max_results=10,
            deadline=past_deadline,
        )

    # Because deadline was expired before start, stream breaks immediately and GraphQL times out before execution
    assert len(calls) == 0
    assert hits == []
    assert len(diags) == 1
    assert diags[0].failure_kind == "network"


@pytest.mark.asyncio
async def test_no_second_attempt_when_deadline_expires_during_backoff() -> None:
    """When remaining budget deadline is shorter than required retry delay, retry is aborted."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "100.0"},  # 100 seconds required wait
            text="Rate limited",
        )

    # Deadline is 0.05 seconds from now
    tight_deadline = time.monotonic() + 0.05
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="repo:^github\\.com/owner/repo$ retry_logic",
            var_name="retry_logic",
            var_kind="lexical",
            max_results=10,
            deadline=tight_deadline,
        )

    # First attempt made; retry aborted because 100s > remaining 0.05s
    assert len(calls) == 1
    assert len(diags) == 1
    assert diags[0].status_code == 429
    assert diags[0].retry_after_seconds == 100.0


@pytest.mark.asyncio
async def test_stream_503_then_deadline_aborts_retry_yields_single_get() -> None:
    """Stream makes 1 GET that returns 503; deadline expires before retry delay, resulting in exactly 1 GET and fallback timeout."""
    stream_calls: list[httpx.Request] = []
    graphql_calls: list[httpx.Request] = []

    # Start with a deadline that will expire after the 1st request completes
    base_time = 1000.0
    current_time = base_time

    def mock_monotonic() -> float:
        nonlocal current_time
        return current_time

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current_time
        if ".api/search/stream" in str(request.url):
            stream_calls.append(request)
            # Advance time past deadline so retry & fallback see expired deadline
            current_time = base_time + 10.0
            return httpx.Response(503, headers={"Retry-After": "50.0"}, text="Service Unavailable")
        if ".api/graphql" in str(request.url):
            graphql_calls.append(request)
            return httpx.Response(200, json=_SAMPLE_GRAPHQL_PAYLOAD)
        raise AssertionError(f"Unexpected request: {request.url}")

    plan, request = _make_request_and_plan()
    deadline = base_time + 1.0  # Deadline is at 1001.0, but handler advances to 1010.0
    transport = httpx.MockTransport(handler)

    with patch("time.monotonic", side_effect=mock_monotonic):
        async with httpx.AsyncClient(transport=transport) as client:
            _, hits, diags, transport_name = await _stream_search_variant(
                client,
                headers={},
                query_variant="repo:^github\\.com/owner/repo$ retry_logic",
                var_name="retry_logic",
                var_kind="lexical",
                max_results=10,
                deadline=deadline,
            )

    # Exactly 1 stream GET made; deadline expired, so retry aborted; GraphQL times out before execution
    assert len(stream_calls) == 1
    assert len(graphql_calls) == 0
    assert hits == []
    assert len(diags) == 1
    assert diags[0].failure_kind == "network"
# 6. Contract and diagnostic invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostic_populates_retry_after_seconds_and_details() -> None:
    """Diagnostic carries retry_after_seconds and additive retry details when exhausted."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "5"},
            text="Too Many Requests",
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="test",
            var_name="test",
            var_kind="lexical",
            max_results=5,
        )

    assert len(calls) == 2
    assert len(diags) == 1
    assert diags[0].status_code == 429
    assert diags[0].retry_after_seconds == 5.0
    assert diags[0].details.get("retries_attempted") == 1


@pytest.mark.asyncio
async def test_malformed_json_not_retried() -> None:
    """Malformed JSON on HTTP 200 is not retried."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="Not a valid JSON {{{")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="test",
            var_name="test",
            var_kind="lexical",
            max_results=5,
        )

    assert len(calls) == 1
    assert hits == []
    assert len(diags) == 1
    assert "invalid JSON" in diags[0].message


@pytest.mark.asyncio
async def test_valid_empty_result_not_retried() -> None:
    """Valid empty result (200 with 0 hits) is not retried."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"data": {"search": {"results": {"matchCount": 0, "limitHit": False, "results": []}}}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        _, hits, diags, transport_name = await _graphql_search_variant(
            client,
            headers={},
            query_variant="test",
            var_name="test",
            var_kind="lexical",
            max_results=5,
        )

    assert len(calls) == 1
    assert hits == []
    assert diags == []


def test_retry_helper_resolution() -> None:
    """_resolve_max_retries consumes catalog retry budget."""
    retries = _resolve_max_retries()
    assert retries >= 1

    # _parse_retry_after_header parses delta seconds and None
    assert _parse_retry_after_header("15") == 15.0
    assert _parse_retry_after_header("0") == 0.0
    assert _parse_retry_after_header(None) is None
    assert _parse_retry_after_header("invalid") is None
