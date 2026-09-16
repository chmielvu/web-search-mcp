"""Abstract base class for all search providers."""
from __future__ import annotations

import asyncio
import os
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

from hsearch.config import get_key, timeout_seconds
from hsearch.models import SearchResult


def _get_version() -> str:
    try:
        from hsearch import __version__
        return __version__
    except Exception:
        return "0.0.0"


class ProviderAuthError(RuntimeError):
    """Raised when a provider rejects auth (401/403)."""


class ProviderHTTPError(RuntimeError):
    """Raised on non-2xx responses we couldn't handle."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


# Status codes we will retry with exponential backoff.
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _default_retries() -> int:
    raw = os.environ.get("HSEARCH_RETRIES", "2")
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


class SearchProvider(ABC):
    """Base class for search providers. Subclasses must set ``name`` and implement ``_search``."""

    name: str = ""
    requires_env: list[str] = []
    supports_extract: bool = False

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds(), connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": f"hsearch/{_get_version()}"},
        )
        # Per-call retries override; can be set via kwargs `_retries`.
        self._retries: int | None = None

    @property
    def api_key(self) -> str | None:
        return get_key(self.name)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "SearchProvider":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # --- Public API ----------------------------------------------------------

    async def search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        """Run a search.  Retries on 429/5xx are handled inside ``_request``."""
        if not self.is_configured():
            raise ProviderAuthError(
                f"{self.name}: missing env {','.join(self.requires_env) or '?'}"
            )
        # Allow callers to override retry count for this call.
        retries = kwargs.pop("_retries", None)
        if retries is not None:
            try:
                self._retries = max(0, int(retries))
            except (TypeError, ValueError):
                self._retries = None
        try:
            return await self._search(query, count=count, **kwargs)
        finally:
            self._retries = None

    async def extract(self, url: str) -> str | None:
        """Optionally implemented by providers that can return page text."""
        if not self.supports_extract:
            return None
        return await self._extract(url)

    # --- Subclass hooks ------------------------------------------------------

    @abstractmethod
    async def _search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        ...

    async def _extract(self, url: str) -> str | None:
        return None

    # --- HTTP helper ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        max_retries = self._retries if self._retries is not None else _default_retries()
        attempt = 0
        last_exc: Exception | None = None
        # Build kwargs for client.request — only include timeout if explicitly
        # provided, so the default AsyncClient timeout remains the fallback.
        req_kwargs: dict[str, Any] = {"headers": headers, "params": params, "json": json}
        if timeout is not None:
            req_kwargs["timeout"] = timeout
        while True:
            try:
                resp = await self._client.request(method, url, **req_kwargs)
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue
                raise ProviderHTTPError(0, f"timeout: {e}") from e
            except httpx.HTTPError as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue
                raise ProviderHTTPError(0, f"network error: {e}") from e

            if resp.status_code in (401, 403):
                raise ProviderAuthError(
                    f"{self.name}: HTTP {resp.status_code} (key invalid or insufficient access)"
                )
            if resp.status_code in _RETRY_STATUSES and attempt < max_retries:
                # Honor Retry-After if the server provides it (seconds).
                retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                delay: float
                if retry_after:
                    try:
                        delay = max(0.0, float(retry_after))
                    except ValueError:
                        delay = self._backoff_delay(attempt)
                else:
                    delay = self._backoff_delay(attempt)
                await asyncio.sleep(delay)
                attempt += 1
                continue
            if resp.status_code >= 400:
                body = resp.text[:300]
                raise ProviderHTTPError(resp.status_code, body)
            return resp

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff: 0.5s, 1s, 2s, ... with small jitter."""
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(0, 0.1)
        return base + jitter
