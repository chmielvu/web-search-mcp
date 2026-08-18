"""MCP tool-design resilience tests: rate-limit retry, provider error contracts,
and per-provider timeout budgets.

Covers the additive fixes in:
- search/providers/base.py        (Retry-After parsing, bounded retry, metadata)
- search/retrieval.py             (warning error contract, per-call timeout cap)
- search/provider_catalog.py      (resilience metadata on definitions)
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.contracts import BranchRole, QueryBranch
from kindly_web_search_mcp_server.search.providers.base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    get_provider_request_metadata,
    run_clientless_provider,
    run_provider,
)


def _result(link: str = "https://example.com/1") -> WebSearchResult:
    return WebSearchResult(title="ok", link=link, snippet="snippet")


def _parse(payload: object) -> list[WebSearchResult]:
    return [_result()]


# ---------------------------------------------------------------------------
# run_provider bounded retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_provider_retries_429_honoring_retry_after() -> None:
    """A 429 with Retry-After is retried once and the retry succeeds with a
    fresh metadata context (no leaked http_status from the failed attempt)."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            response = httpx.Response(429, headers={"Retry-After": "0.05"}, json={"error": "slow down"})
        else:
            response = httpx.Response(200, json={"ok": True})
        response.request = request
        return response

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        response = await client.get("https://provider.example/search")
        response.raise_for_status()
        return response.json()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_provider(
            "probe-retry",
            "query",
            1,
            request=request,
            parse_response=_parse,
            http_client=client,
            max_retries=1,
        )

    assert len(results) == 1
    assert len(calls) == 2
    metadata = get_provider_request_metadata()
    assert metadata is not None
    assert metadata.result_class == "nonempty"
    assert metadata.http_status is None
    assert metadata.error_type is None


@pytest.mark.asyncio
async def test_run_provider_retries_429_with_http_date_retry_after() -> None:
    """Retry-After expressed as an HTTP-date (RFC 9110) is parsed and honored."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            # format_datetime truncates to whole seconds, so use a margin that
            # survives truncation plus parse latency (2s still keeps the test
            # fast while proving the HTTP-date path is honored).
            retry_at = format_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=2), usegmt=True
            )
            response = httpx.Response(429, headers={"Retry-After": retry_at}, json={})
        else:
            response = httpx.Response(200, json={"ok": True})
        response.request = request
        return response

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        response = await client.get("https://provider.example/search")
        response.raise_for_status()
        return response.json()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_provider(
            "probe-date",
            "query",
            1,
            request=request,
            parse_response=_parse,
            http_client=client,
            max_retries=1,
        )

    assert len(results) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_provider_uses_catalog_retry_default() -> None:
    """A provider name present in the catalog retries by default (no explicit
    ``max_retries``), proving ``provider_retry_max_retries`` resolves the
    catalog entry instead of silently defaulting to zero."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            response = httpx.Response(429, headers={"Retry-After": "0.01"}, json={})
        else:
            response = httpx.Response(200, json={"ok": True})
        response.request = request
        return response

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        response = await client.get("https://provider.example/search")
        response.raise_for_status()
        return response.json()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_provider(
            "searxng",  # catalog: max_retries=1
            "query",
            1,
            request=request,
            parse_response=_parse,
            http_client=client,
        )

    assert len(results) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_provider_does_not_retry_auth_failure() -> None:
    """403 (auth) is never retried — retrying only deepens the block."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        response = httpx.Response(403, json={"error": "forbidden"})
        response.request = request
        return response

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        response = await client.get("https://provider.example/search")
        response.raise_for_status()
        return response.json()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError) as excinfo:
            await run_provider(
                "probe-auth",
                "query",
                1,
                request=request,
                parse_response=_parse,
                http_client=client,
                max_retries=3,
            )

    assert excinfo.value.metadata is not None
    assert excinfo.value.metadata.error_type == "auth"
    assert excinfo.value.metadata.retryable is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_run_provider_merges_provider_raised_metadata() -> None:
    """Provider-raised ProviderRequestError metadata (e.g. SearxngError) is
    merged back into the request context so retrieval sees http_status."""
    class _ProbeError(ProviderRequestError):
        pass

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        raise _ProbeError(
            "rate limited",
            metadata=ProviderRequestMetadata(
                provider="probe",
                http_status=429,
                result_class="error",
                error_type="rate_limit",
                error_summary="rate limited",
                retry_after=1.5,
                retryable=True,
            ),
        )

    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderRequestError) as excinfo:
            await run_provider(
                "probe",
                "query",
                1,
                request=request,
                parse_response=_parse,
                http_client=client,
            )

    assert excinfo.value.metadata is not None
    assert excinfo.value.metadata.http_status == 429
    assert excinfo.value.metadata.retry_after == 1.5
    merged = get_provider_request_metadata()
    assert merged is not None
    assert merged.http_status == 429
    assert merged.retry_after == 1.5


# ---------------------------------------------------------------------------
# run_clientless_provider error contract
# ---------------------------------------------------------------------------


class _FakeStatusError(Exception):
    status_code = 429
    response = httpx.Response(429, headers={"Retry-After": "0.05"})


@pytest.mark.asyncio
async def test_run_clientless_provider_classifies_rate_limit() -> None:
    """Clientless adapters (e.g. Qdrant) normalize raw SDK errors into the
    provider error contract instead of leaking bare exceptions."""

    async def request() -> None:
        raise _FakeStatusError("boom")

    with pytest.raises(ProviderRequestError) as excinfo:
        await run_clientless_provider(
            "probe",
            "query",
            1,
            request=request,
            parse_response=_parse,
            max_retries=0,
        )

    metadata = excinfo.value.metadata
    assert metadata is not None
    assert metadata.error_type == "rate_limit"
    assert metadata.retry_after == 0.05
    assert metadata.retryable is True
    assert metadata.result_class == "error"


# ---------------------------------------------------------------------------
# retrieval warning error contract
# ---------------------------------------------------------------------------


def _branch(*providers: str) -> QueryBranch:
    return QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
        query="probe query",
        provider_names=providers,
        why="",
        support_terms=(),
        max_results=5,
    )


def test_warning_rate_limit_carries_contract_fields() -> None:
    from kindly_web_search_mcp_server.search.retrieval import _record_provider_result

    warnings_by_name: dict[str, object] = {}
    metadata = ProviderRequestMetadata(
        provider="probe",
        http_status=429,
        result_class="error",
        error_type="rate_limit",
        error_summary="rate limited by upstream",
        retry_after=2.5,
        retryable=True,
    )
    _record_provider_result(
        branch=_branch("probe"),
        branch_index=0,
        name="probe",
        value=ProviderRequestError("rate limited", metadata=metadata),
        latency_ms=10.0,
        rows=OrderedDict(),
        warnings_by_name=warnings_by_name,
        provider_calls=[],
        provider_ranked_results_list=[],
        metadata=metadata,
    )

    warning = warnings_by_name["probe"]
    assert warning.error_type == "rate_limit"
    assert warning.error == "rate limited by upstream"  # real message, not the type
    assert warning.retry_after == 2.5
    assert warning.retryable is True
    assert warning.action is not None
    assert "wait" in warning.action.lower()


def test_warning_timeout_is_retryable_with_hint() -> None:
    from kindly_web_search_mcp_server.search.retrieval import _record_provider_result

    warnings_by_name: dict[str, object] = {}
    metadata = ProviderRequestMetadata(
        provider="probe",
        result_class="timeout",
        error_type="timeout",
        error_summary="provider request timed out",
        retryable=True,
    )
    _record_provider_result(
        branch=_branch("probe"),
        branch_index=0,
        name="probe",
        value=TimeoutError(),
        latency_ms=2000.0,
        rows=OrderedDict(),
        warnings_by_name=warnings_by_name,
        provider_calls=[],
        provider_ranked_results_list=[],
        metadata=metadata,
    )

    warning = warnings_by_name["probe"]
    assert warning.error_type == "timeout"
    assert warning.retryable is True
    assert warning.action is not None
    assert "transient" in warning.action.lower()


def test_warning_budget_exhausted_is_not_retryable() -> None:
    from kindly_web_search_mcp_server.search.retrieval import _record_provider_result

    warnings_by_name: dict[str, object] = {}
    _record_provider_result(
        branch=_branch("probe"),
        branch_index=0,
        name="probe",
        value=None,
        latency_ms=5000.0,
        rows=OrderedDict(),
        warnings_by_name=warnings_by_name,
        provider_calls=[],
        provider_ranked_results_list=[],
        status_override="incomplete",
        metadata=ProviderRequestMetadata(
            provider="probe",
            result_class="incomplete",
            error_type="retrieve_budget",
            error_summary="retrieve budget exhausted",
        ),
    )

    warning = warnings_by_name["probe"]
    assert warning.error_type == "retrieve_budget"
    assert warning.retryable is False
    assert warning.action is not None
    assert "budget" in warning.action.lower()


# ---------------------------------------------------------------------------
# catalog resilience metadata
# ---------------------------------------------------------------------------


def test_catalog_carries_resilience_metadata() -> None:
    from kindly_web_search_mcp_server.search.provider_catalog import (
        PROVIDER_DEFINITIONS_LIST,
    )
    from kindly_web_search_mcp_server.search.provider_registry import (
        PROVIDER_DEFINITIONS,
    )

    for name in ("searxng", "tavily", "qdrant", "gemma", "serpapi"):
        definition = PROVIDER_DEFINITIONS[name]
        assert definition.max_retries >= 1, name
        assert definition.cooldown_seconds is not None, name

    # Telegram flood control is adapter-owned; the pipeline never retries it.
    assert PROVIDER_DEFINITIONS["telegram"].max_retries == 0

    # Caps, when set, are positive and do not exceed the global budget by fiat.
    for definition in PROVIDER_DEFINITIONS_LIST:
        assert definition.retryable is True
        if definition.per_call_timeout_seconds is not None:
            assert definition.per_call_timeout_seconds > 0
        assert definition.max_retries >= 0
