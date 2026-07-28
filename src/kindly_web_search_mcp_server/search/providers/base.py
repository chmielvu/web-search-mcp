"""Shared provider execution helpers for search providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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


class ProviderRequestError(RuntimeError):
    """Normalized provider failure retaining request diagnostics."""

    def __init__(self, message: str, *, metadata: ProviderRequestMetadata) -> None:
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


def _response_metadata(response: httpx.Response) -> dict[str, object]:
    """Keep bounded, non-sensitive headers useful for provider diagnostics."""
    metadata: dict[str, object] = {}
    for key in (
        "retry-after",
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


async def run_provider(
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request: RequestFn[TResponse],
    parse_response: ParseFn[TResponse],
    http_client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
) -> list[WebSearchResult]:
    """Execute a provider request, client lifecycle, and normalization."""
    if not query.strip() or num_results < 1:
        return []

    # Every invocation gets a fresh request context. Provider-specific
    # metadata is initialized inside the request callback below, so fields
    # from a previous call cannot leak into this request.
    set_provider_request_metadata(ProviderRequestMetadata(provider=provider_name))

    if timeout_seconds is None:
        timeout_seconds = settings.search_retrieve_budget_seconds

    async def _fetch(client: httpx.AsyncClient) -> list[WebSearchResult]:
        try:
            payload = await request(client)
            results = parse_response(payload)
        except ProviderRequestError:
            raise
        except httpx.TimeoutException as exc:
            metadata = get_provider_request_metadata() or ProviderRequestMetadata(provider_name)
            metadata = _with_metadata(
                metadata,
                result_class="timeout",
                error_type="timeout",
                error_summary=str(exc)[:500],
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
            metadata = _with_metadata(
                metadata,
                endpoint=endpoint,
                http_status=response.status_code,
                result_class="error",
                error_type=_classify_http_status(response.status_code),
                error_summary=error_summary[:500],
                response_meta=response_meta,
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
            metadata = _with_metadata(
                metadata,
                result_class="error",
                http_status=status_code,
                error_type=(
                    _classify_http_status(status_code)
                    if isinstance(status_code, int)
                    else type(exc).__name__
                ),
                error_summary=str(exc)[:500],
                response_meta=response_meta,
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

    if http_client is not None:
        return await _fetch(http_client)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
    ) as client:
        return await _fetch(client)


async def run_clientless_provider(
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request: ClientlessRequestFn[TResponse],
    parse_response: ParseFn[TResponse],
) -> list[WebSearchResult]:
    """Execute a provider request without a shared HTTP client."""
    if not query.strip() or num_results < 1:
        return []

    payload = await request()
    results = parse_response(payload)
    return _attach_provider_name(results, provider_name)[:num_results]
