"""Shared provider execution helpers for search providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from ..models import WebSearchResult
from ..retry import retry_with_backoff

TResponse = TypeVar("TResponse")

RequestFn = Callable[[httpx.AsyncClient], Awaitable[TResponse]]
ClientlessRequestFn = Callable[[], Awaitable[TResponse]]
ParseFn = Callable[[TResponse], list[WebSearchResult]]


def _attach_provider_name(
    results: list[WebSearchResult],
    provider_name: str,
) -> list[WebSearchResult]:
    return [
        result.model_copy(
            update={
                "providers": sorted({*(result.providers or []), provider_name}),
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
    timeout_seconds: float = 30.0,
) -> list[WebSearchResult]:
    """Execute a provider request with retries, client lifecycle, and normalization."""
    if not query.strip() or num_results < 1:
        return []

    async def _fetch(client: httpx.AsyncClient) -> list[WebSearchResult]:
        payload = await retry_with_backoff(
            lambda: request(client),
            provider_name=provider_name,
            max_retries=2,
        )
        results = parse_response(payload)
        return _attach_provider_name(results, provider_name)[:num_results]

    if http_client is not None:
        return await _fetch(http_client)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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

    payload = await retry_with_backoff(
        request,
        provider_name=provider_name,
        max_retries=2,
    )
    results = parse_response(payload)
    return _attach_provider_name(results, provider_name)[:num_results]
