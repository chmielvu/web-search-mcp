"""SingleFlight: coalesce identical concurrent requests into one execution.

When multiple callers request the same operation concurrently, only one
execution runs. All other callers receive the same result (or exception).
This is the asyncio equivalent of Go's singleflight.Group.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class SingleFlight:
    """Coalesce identical concurrent async operations."""

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future[Any]] = {}

    @staticmethod
    def make_key(*parts: object) -> str:
        """Build a deterministic key from arbitrary parts."""
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def do(
        self,
        key: str,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute *fn* once for a given *key*, sharing the result with waiters.

        If another coroutine is already executing under the same key, this
        call awaits the existing future instead of starting a new execution.
        """
        if key in self._in_flight:
            logger.debug("SingleFlight: coalescing request for key=%s", key[:16])
            return await asyncio.shield(self._in_flight[key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._in_flight[key] = future

        try:
            result = await fn(*args, **kwargs)
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            self._in_flight.pop(key, None)
