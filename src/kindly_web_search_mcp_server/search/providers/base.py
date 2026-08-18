"""Shared provider execution helpers for search providers."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx
from dataclasses import dataclass, field
import contextvars

from ...models import WebSearchResult
from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url

TResponse = TypeVar("TResponse")

RequestFn = Callable[[httpx.AsyncClient], Awaitable[TResponse]]
ClientlessRequestFn = Callable[[], Awaitable[TResponse]]
ParseFn = Callable[[TResponse], list[WebSearchResult]]


@dataclass(frozen=True, slots=True)
class ProviderRequestMetadata:
    provider: str
    endpoint: str | None = None
    http_status: int | None = None
    result_class: str | None = None
    error_type: str | None = None
    error_summary: str | None = None
    auth_mode: str | None = None
    response_meta: dict[str, object] = field(default_factory=dict)
    retry_after: float | None = None
    retryable: bool | None = None


class ProviderRequestError(RuntimeError):
    """Normalized provider failure retaining request diagnostics."""

    def __init__(self, message: str, *, metadata: ProviderRequestMetadata | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata

_provider_metadata_context: contextvars.ContextVar[ProviderRequestMetadata | None] = (
    contextvars.ContextVar("provider_request_metadata", default=None)
)


def set_provider_request_metadata(metadata: ProviderRequestMetadata) -> None:
    _provider_metadata_context.set(metadata)


def get_provider_request_metadata() -> ProviderRequestMetadata | None:
    return _provider_metadata_context.get()


def _with_metadata(
    metadata: ProviderRequestMetadata,
    **updates: object,
) -> ProviderRequestMetadata:
    values = {
        "provider": metadata.provider,
        "endpoint": metadata.endpoint,
        "http_status": metadata.http_status,
        "result_class": metadata.result_class,
        "error_type": metadata.error_type,
        "error_summary": metadata.error_summary,
        "auth_mode": metadata.auth_mode,
        "response_meta": metadata.response_meta,
        "retry_after": metadata.retry_after,
        "retryable": metadata.retryable,
    }
    values.update(updates)
    return ProviderRequestMetadata(**values)


def _classify_http_status(status_code: int) -> str:
    if status_code in {401, 403, 407}:
        return "auth"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "upstream"
    return "http_status"


# Statuses worth a bounded retry when a search request was not served:
# 408/425 are "try again", 429 is rate-limit, 5xx are upstream failures.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NETWORK_ERROR_TYPES = frozenset(
    {
        "ConnectError",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "NetworkError",
    }
)


def _is_retryable_metadata(metadata: ProviderRequestMetadata | None) -> bool:
    """Decide whether a failed provider call is safe/valuable to retry.

    Server-issued ``retryable`` metadata wins; otherwise infer from the
    HTTP status (429/5xx/408/425 are transient) or the exception class
    (transport errors and timeouts are transient). Auth, content, and
    budget failures are never retried.
    """
    if metadata is None:
        return False
    if metadata.retryable is not None:
        return metadata.retryable
    if metadata.http_status is not None:
        return metadata.http_status in _RETRYABLE_HTTP_STATUSES
    if metadata.error_type is not None:
        return metadata.error_type in _NETWORK_ERROR_TYPES or metadata.error_type == "timeout"
    return False


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header: delta-seconds or an HTTP-date.

    Mirrors RFC 9110: an integer/float is seconds from now; a date is the
    absolute time the limit resets. Returns ``None`` when unparsable so
    callers fall back to exponential backoff.
    """
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


def _response_metadata(response: httpx.Response) -> dict[str, object]:
    """Keep bounded, non-sensitive headers useful for provider diagnostics."""
    metadata: dict[str, object] = {}
    for key in (
        "retry-after",
        "x-ratelimit-remaining",
        "x-ratelimit-limit",
        "x-ratelimit-reset",
        "x-ratelimit-type",
        "x-brd-err-msg",
        "proxy-status",
        "x-request-id",
        "x-response-id",
        "content-type",
    ):
        value = response.headers.get(key)
        if value:
            metadata[key.replace("-", "_")] = value[:500]
    return metadata


def _attach_provider_name(
    results: list[WebSearchResult],
    provider_name: str,
) -> list[WebSearchResult]:
    return [
        result.model_copy(
            update={
                "providers": sorted({*(result.providers or []), provider_name}),
                "domain": result.domain or extract_domain_from_url(result.link),
            }
        )
        for result in results
    ]


def provider_retry_max_retries(provider_name: str) -> int:
    """Return the catalog-configured retry count for a provider, 0 when unknown.

    Resolved lazily to avoid an import cycle at module load; adapters pass an
    explicit ``max_retries`` when they need to override the catalog default.
    """
    try:
        # PROVIDER_DEFINITIONS (name -> definition) is assembled in the
        # registry; the catalog only exposes PROVIDER_DEFINITIONS_LIST.
        from ..provider_registry import PROVIDER_DEFINITIONS

        definition = PROVIDER_DEFINITIONS.get(provider_name)
    except Exception:
        return 0
    return definition.max_retries if definition is not None else 0


async def run_provider(
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request: RequestFn[TResponse],
    parse_response: ParseFn[TResponse],
    http_client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> list[WebSearchResult]:
    """Execute a provider request, client lifecycle, and normalization.

    Retries transient failures (429 with Retry-After, 408/425/5xx, transport
    errors) with exponential backoff + jitter when ``max_retries > 0``. A
    server-issued ``Retry-After`` (delta-seconds or HTTP-date) is honored and
    the total attempt window is capped by ``timeout_seconds``. ``None`` for
    ``max_retries`` resolves the catalog default (0 = single attempt).
    """
    if not query.strip() or num_results < 1:
        return []

    # Every invocation gets a fresh request context. Provider-specific
    # metadata is initialized inside the request callback below, so fields
    # from a previous call cannot leak into this request.
    set_provider_request_metadata(ProviderRequestMetadata(provider=provider_name))

    if timeout_seconds is None:
        timeout_seconds = settings.search_retrieve_budget_seconds
    if max_retries is None:
        max_retries = provider_retry_max_retries(provider_name)
    deadline = time.monotonic() + timeout_seconds

    async def _fetch(client: httpx.AsyncClient) -> list[WebSearchResult]:
        try:
            payload = await request(client)
            results = parse_response(payload)
        except ProviderRequestError as exc:
            # Merge provider-raised metadata (e.g. SearxngError carrying its
            # own http_status/retry_after) back into the request context so
            # the retrieval layer sees the real failure, not a bare message.
            merged = exc.metadata or get_provider_request_metadata()
            if merged is not None:
                set_provider_request_metadata(merged)
            raise
        except httpx.TimeoutException as exc:
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
            metadata = _with_metadata(
                metadata,
                result_class="timeout",
                error_type="timeout",
                error_summary=str(exc)[:500],
                retryable=True,
            )
            set_provider_request_metadata(metadata)
            raise ProviderRequestError(str(exc) or "provider request timed out", metadata=metadata)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
            try:
                endpoint = str(response.request.url)
            except RuntimeError:
                endpoint = None
            response_meta = dict(metadata.response_meta)
            response_meta.update(_response_metadata(response))
            error_summary = (
                response.headers.get("x-brd-err-msg")
                or response.headers.get("proxy-status")
                or str(exc)
            )
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            status = response.status_code
            error_type = _classify_http_status(status)
            metadata = _with_metadata(
                metadata,
                endpoint=endpoint,
                http_status=status,
                result_class="error",
                error_type=error_type,
                error_summary=error_summary[:500],
                response_meta=response_meta,
                retry_after=retry_after,
                retryable=status in _RETRYABLE_HTTP_STATUSES,
            )
            set_provider_request_metadata(metadata)
            raise ProviderRequestError(str(exc), metadata=metadata) from exc
        except Exception as exc:
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
            response_meta = dict(metadata.response_meta)
            upstream_meta = getattr(exc, "response_meta", None)
            if isinstance(upstream_meta, dict):
                response_meta.update(upstream_meta)
            status_code = getattr(exc, "status_code", None)
            if not isinstance(status_code, int):
                status_code = metadata.http_status
            error_type = (
                _classify_http_status(status_code)
                if isinstance(status_code, int)
                else type(exc).__name__
            )
            metadata = _with_metadata(
                metadata,
                result_class="error",
                http_status=status_code,
                error_type=error_type,
                error_summary=str(exc)[:500],
                response_meta=response_meta,
                retryable=isinstance(exc, httpx.TransportError),
            )
            set_provider_request_metadata(metadata)
            raise ProviderRequestError(str(exc), metadata=metadata) from exc
        attached = _attach_provider_name(results, provider_name)[:num_results]
        metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
        metadata = _with_metadata(
            metadata,
            result_class="nonempty" if attached else "empty",
        )
        set_provider_request_metadata(metadata)
        return attached

    async def _attempt(client: httpx.AsyncClient) -> list[WebSearchResult]:
        try:
            return await _fetch(client)
        except ProviderRequestError as exc:
            if max_retries <= 0 or not _is_retryable_metadata(exc.metadata):
                raise
            # No point sleeping on the final attempt — propagate the failure.
            if attempt >= max_retries:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            retry_after = exc.metadata.retry_after if exc.metadata else None
            if retry_after is not None:
                # RFC 9110: Retry-After 0 means "retry immediately"; a
                # clamp-to-zero from an elapsed HTTP-date is equivalent.
                delay = max(0.0, min(retry_after, 30.0, remaining))
            else:
                # Exponential backoff with full jitter, capped by the budget.
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), remaining)
            if delay < 0:
                raise
            if delay > 0:
                await asyncio.sleep(delay)
            # Fresh request context per retry attempt: a failed attempt's
            # http_status/error fields must not leak into the next attempt.
            set_provider_request_metadata(ProviderRequestMetadata(provider=provider_name))
            return None  # signal: retry

    if http_client is not None:
        for attempt in range(max_retries + 1):
            result = await _attempt(http_client)
            if result is not None:
                return result
        raise RuntimeError("unreachable: _attempt always returns or raises")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
    ) as client:
        for attempt in range(max_retries + 1):
            result = await _attempt(client)
            if result is not None:
                return result
        raise RuntimeError("unreachable: _attempt always returns or raises")


async def run_clientless_provider(
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request: ClientlessRequestFn[TResponse],
    parse_response: ParseFn[TResponse],
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> list[WebSearchResult]:
    """Execute a provider request without a shared HTTP client.

    Same error-contract and bounded-retry semantics as ``run_provider`` so
    clientless adapters (e.g. Qdrant) surface ``http_status``/``retry_after``
    instead of leaking raw SDK exceptions.
    """
    if not query.strip() or num_results < 1:
        return []

    set_provider_request_metadata(ProviderRequestMetadata(provider=provider_name))
    if timeout_seconds is None:
        timeout_seconds = settings.search_retrieve_budget_seconds
    if max_retries is None:
        max_retries = provider_retry_max_retries(provider_name)
    deadline = time.monotonic() + timeout_seconds

    async def _fetch() -> list[WebSearchResult]:
        try:
            payload = await request()
            results = parse_response(payload)
        except ProviderRequestError as exc:
            merged = exc.metadata or get_provider_request_metadata()
            if merged is not None:
                set_provider_request_metadata(merged)
            raise
        except Exception as exc:
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
            status_code = getattr(exc, "status_code", None)
            response_meta = dict(metadata.response_meta)
            upstream_meta = getattr(exc, "response_meta", None)
            if isinstance(upstream_meta, dict):
                response_meta.update(upstream_meta)
            retry_after: float | None = None
            response = getattr(exc, "response", None)
            if response is not None and hasattr(response, "headers"):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            error_type = (
                _classify_http_status(status_code)
                if isinstance(status_code, int)
                else type(exc).__name__
            )
            metadata = _with_metadata(
                metadata,
                result_class="error",
                http_status=status_code if isinstance(status_code, int) else None,
                error_type=error_type,
                error_summary=str(exc)[:500],
                response_meta=response_meta,
                retry_after=retry_after,
                retryable=(
                    status_code in _RETRYABLE_HTTP_STATUSES
                    if isinstance(status_code, int)
                    else isinstance(exc, (httpx.TransportError, httpx.TimeoutException))
                ),
            )
            set_provider_request_metadata(metadata)
            raise ProviderRequestError(str(exc), metadata=metadata) from exc
        attached = _attach_provider_name(results, provider_name)[:num_results]
        metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
        metadata = _with_metadata(
            metadata,
            result_class="nonempty" if attached else "empty",
        )
        set_provider_request_metadata(metadata)
        return attached

    async def _attempt() -> list[WebSearchResult]:
        try:
            return await _fetch()
        except ProviderRequestError as exc:
            if max_retries <= 0 or not _is_retryable_metadata(exc.metadata):
                raise
            if attempt >= max_retries:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            retry_after = exc.metadata.retry_after if exc.metadata else None
            if retry_after is not None:
                delay = max(0.0, min(retry_after, 30.0, remaining))
            else:
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), remaining)
            if delay < 0:
                raise
            if delay > 0:
                await asyncio.sleep(delay)
            set_provider_request_metadata(ProviderRequestMetadata(provider=provider_name))
            return None  # signal: retry

    for attempt in range(max_retries + 1):
        result = await _attempt()
        if result is not None:
            return result
    raise RuntimeError("unreachable: _attempt always returns or raises")
