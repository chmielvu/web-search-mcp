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

    Args:
        fn: Async function to execute (no arguments, returns Awaitable)
        max_retries: Maximum retry attempts (default: 2, so 3 total attempts)
        initial_delay_ms: Initial delay in milliseconds (default: 1000)
        max_delay_ms: Maximum delay cap in milliseconds (default: 10000)
        backoff_factor: Multiplier for each retry (default: 2.0)
        provider_name: Optional provider name for logging

    Returns:
        Result of the function if successful

    Raises:
        The last exception if all retries exhausted or non-transient error
    """
    delay_ms = initial_delay_ms
    last_error: Exception | None = None
    provider_label = provider_name or "provider"

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_error = e

            # Check if error is transient and we have retries left
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

            # Calculate delay with exponential backoff
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

    # Should never reach here, but raise last error as fallback
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
