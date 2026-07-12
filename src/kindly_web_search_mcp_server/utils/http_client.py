"""Server-level shared HTTP client.

The search pipeline creates many short-lived requests to external providers.
Creating and closing an ``httpx.AsyncClient`` for every request is expensive,
does not reuse TCP connections, and forces the orchestrator to coordinate
client closure with in-flight cancellation cleanup.  A single long-lived
client solves all of those problems.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Literal, TypeVar

import httpx

from ..settings import settings

T = TypeVar("T")

_lock: asyncio.Lock | None = None
_shared_client: httpx.AsyncClient | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def build_http_client() -> httpx.AsyncClient:
    """Build a configured ``httpx.AsyncClient`` from settings."""
    read_timeout = settings.search_http_read_timeout_seconds
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.search_http_connect_timeout_seconds,
            read=read_timeout,
            write=read_timeout,
            pool=read_timeout,
        ),
        follow_redirects=True,
        # Keep connections alive for reuse across requests.
        limits=httpx.Limits(
            max_connections=200,
            max_keepalive_connections=50,
        ),
    )


async def get_http_client() -> httpx.AsyncClient:
    """Return the singleton async HTTP client, creating it on first call."""
    global _shared_client
    if _shared_client is None:
        async with _get_lock():
            if _shared_client is None:
                _shared_client = build_http_client()
    return _shared_client


async def close_http_client() -> None:
    """Close the singleton client if it exists.  Safe to call multiple times."""
    global _shared_client
    if _shared_client is None:
        return
    async with _get_lock():
        if _shared_client is not None:
            await _shared_client.aclose()
            _shared_client = None


def reset_http_client_for_tests(client: httpx.AsyncClient | None = None) -> None:
    """Replace the singleton client, used only by tests."""
    global _shared_client
    _shared_client = client


class OutboundCallError(RuntimeError):
    """Sanitized outbound transport or response failure."""

    def __init__(
        self,
        *,
        provider: str,
        category: Literal["timeout", "network", "http_status", "invalid_json", "invalid_shape"],
        method: str,
        url: str,
        status_code: int | None = None,
    ) -> None:
        parsed = httpx.URL(url)
        self.provider = provider
        self.category = category
        self.status_code = status_code
        self.method = method.upper()
        self.host = parsed.host or ""
        self.path = parsed.path
        super().__init__(f"{provider} {category}: {self.method} {self.host}{self.path}")


async def request_json_value(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    expected_type: type[T],
    params: Mapping[str, Any] | None = None,
    json_body: Any | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> T:
    try:
        response = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        raise OutboundCallError(
            provider=provider, category="timeout", method=method, url=url
        ) from None
    except httpx.RequestError:
        raise OutboundCallError(
            provider=provider, category="network", method=method, url=url
        ) from None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise OutboundCallError(
            provider=provider,
            category="http_status",
            method=method,
            url=url,
            status_code=response.status_code,
        ) from None
    try:
        value = response.json()
    except ValueError:
        raise OutboundCallError(
            provider=provider, category="invalid_json", method=method, url=url
        ) from None
    if not isinstance(value, expected_type):
        raise OutboundCallError(
            provider=provider, category="invalid_shape", method=method, url=url
        ) from None
    return value


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    params: Mapping[str, Any] | None = None,
    json_body: Any | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> dict[str, Any]:
    return await request_json_value(
        client,
        method,
        url,
        provider=provider,
        expected_type=dict,
        params=params,
        json_body=json_body,
        headers=headers,
        timeout=timeout,
    )
