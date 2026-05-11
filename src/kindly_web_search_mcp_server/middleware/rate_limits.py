"""Differentiated per-tool rate limiting middleware."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext


@dataclass
class _BucketState:
    tokens: float
    last_refill_ts: float = field(default_factory=time.monotonic)


class _TokenBucketLimiter:
    """Simple async token-bucket limiter."""

    def __init__(self, requests_per_second: float, burst_capacity: int):
        self._rps = max(0.0, requests_per_second)
        self._capacity = max(1, burst_capacity)
        self._state = _BucketState(tokens=float(self._capacity))
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._rps <= 0:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._state.last_refill_ts
                self._state.last_refill_ts = now
                refill = elapsed * self._rps
                self._state.tokens = min(self._capacity, self._state.tokens + refill)

                if self._state.tokens >= 1.0:
                    self._state.tokens -= 1.0
                    return

                deficit = 1.0 - self._state.tokens
                wait_seconds = deficit / self._rps if self._rps > 0 else 0.0

            await asyncio.sleep(wait_seconds)


class DifferentiatedRateLimitMiddleware(Middleware):
    """Apply distinct token buckets to cheap and expensive tool groups."""

    CHEAP_TOOLS = frozenset({"web_search", "get_content", "gemini_search"})
    EXPENSIVE_TOOLS = frozenset({"perplexity_search"})

    def __init__(
        self,
        cheap_rps: float,
        cheap_burst: int,
        expensive_rps: float,
        expensive_burst: int,
    ) -> None:
        self._cheap_limiter = _TokenBucketLimiter(cheap_rps, cheap_burst)
        self._expensive_limiter = _TokenBucketLimiter(expensive_rps, expensive_burst)

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        tool_name = context.message.name
        if tool_name in self.EXPENSIVE_TOOLS:
            await self._expensive_limiter.acquire()
        elif tool_name in self.CHEAP_TOOLS:
            await self._cheap_limiter.acquire()
        return await call_next(context)


def create_differentiated_rate_limit_middleware(
    cheap_rps: float,
    cheap_burst: int,
    expensive_rps: float,
    expensive_burst: int,
) -> DifferentiatedRateLimitMiddleware:
    return DifferentiatedRateLimitMiddleware(
        cheap_rps=cheap_rps,
        cheap_burst=cheap_burst,
        expensive_rps=expensive_rps,
        expensive_burst=expensive_burst,
    )
