"""Retry utilities with exponential backoff for transient errors.

P1 Critical Pattern: retryWithBackoff from Exa MCP
- Only retries on transient errors (5xx, TimeoutException, NetworkError)
- Does NOT retry on client errors (4xx) - these fail immediately
- Exponential backoff with configurable parameters
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, Callable, TypeVar

import httpx

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient and should be retried.

    Retry on:
    - HTTP 5xx errors (server-side failures)
    - httpx.TimeoutException (request timed out)
    - httpx.NetworkError (connection issues)

    Do NOT retry on:
    - HTTP 4xx errors (client errors: auth, bad request, not found)
    - Other exceptions (config errors, parsing errors, etc.)
    """
    if isinstance(error, httpx.TimeoutException):
        return True

    if isinstance(error, httpx.NetworkError):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 429:
            return True
        # Only retry on server errors (5xx)
        # Client errors (4xx) indicate permanent failures
        return 500 <= status < 600

    return False


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 2,
    initial_delay_ms: int = 1000,
    max_delay_ms: int = 10000,
    backoff_factor: float = 2.0,
    provider_name: str | None = None,
) -> T:
    """Execute an async function with exponential backoff retry for transient errors.

    Each retry is guarded by ``asyncio.sleep`` which is a cancellation point.
    If the task is cancelled between retries, the ``CancelledError`` propagates
    immediately rather than being suppressed by the retry loop.
    """
    delay_ms = initial_delay_ms
    last_error: Exception | None = None
    provider_label = provider_name or "provider"

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_error = e

            if not is_transient_error(e):
                LOGGER.debug(
                    "%s: Non-transient error on attempt %d, not retrying: %s",
                    provider_label,
                    attempt + 1,
                    str(e)[:100],
                )
                raise

            if attempt >= max_retries:
                LOGGER.warning(
                    "%s: All %d retries exhausted for transient error: %s",
                    provider_label,
                    max_retries + 1,
                    str(e)[:100],
                )
                raise

            current_delay_ms = min(delay_ms, max_delay_ms)
            delay_seconds = current_delay_ms / 1000.0

            LOGGER.info(
                "%s: Transient error on attempt %d, retrying in %.1fs: %s",
                provider_label,
                attempt + 1,
                delay_seconds,
                str(e)[:80],
            )

            await asyncio.sleep(delay_seconds)
            delay_ms = int(delay_ms * backoff_factor)

    if last_error:
        raise last_error
    raise RuntimeError("retry_with_backoff: unexpected state - no error captured")


def retry_decorator(
    *,
    max_retries: int = 2,
    initial_delay_ms: int = 1000,
    max_delay_ms: int = 10000,
    backoff_factor: float = 2.0,
    provider_name: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator factory for retry_with_backoff.

    Usage:
        @retry_decorator(provider_name="searxng")
        async def search_searxng(...):
            ...
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async def execute() -> T:
                return await fn(*args, **kwargs)

            return await retry_with_backoff(
                execute,
                max_retries=max_retries,
                initial_delay_ms=initial_delay_ms,
                max_delay_ms=max_delay_ms,
                backoff_factor=backoff_factor,
                provider_name=provider_name or fn.__name__,
            )

        return wrapper

    return decorator
