"""Remote HTTP clients for content rendering: Crawl4AI `/md` (cloud markdown) + Camoufox `/content` (stealth-Firefox raw HTML).

Singleton pattern: one client instance per backend, lazy-initialized from settings.

Crawl4AI usage::

    client = get_crawl4ai_client()
    if client is not None:
        markdown = await client.fetch_markdown("https://example.com")

Camoufox usage::

    client = get_camoufox_client()
    if client is not None:
        html = await client.fetch_html("https://example.com")
"""

from __future__ import annotations

import json
import asyncio
import logging
import time
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Crawl4AI client
# ------------------------------------------------------------------


class Crawl4AIClientError(RuntimeError):
    """Raised when a Crawl4AI remote call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Crawl4AIClient:
    """HTTP client for remote Crawl4AI Docker server (POST /md only — non-browser cloud markdown)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        health_cache_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._health_cache_seconds = health_cache_seconds
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        )
        self._health_cache: tuple[float, bool] | None = None

    async def fetch_markdown(
        self,
        url: str,
        *,
        mode: str = "fit",
        query: str | None = None,
    ) -> str:
        """POST /md — clean markdown extraction."""
        payload: dict[str, Any] = {"url": url, "f": mode, "c": "0"}
        if query and mode == "bm25":
            payload["q"] = query
        data = await self._post_json("/md", payload)
        if isinstance(data, dict):
            markdown = (
                data.get("markdown")
                or data.get("content")
                or data.get("result")
                or json.dumps(data)
            )
        elif isinstance(data, str):
            markdown = data
        else:
            markdown = str(data)
        if not markdown.strip():
            raise Crawl4AIClientError("Crawl4AI /md returned empty content")
        return markdown

    async def health_check(self) -> bool:
        """GET /health — check VPS availability (cached)."""
        now = time.monotonic()
        if self._health_cache is not None:
            cached_time, cached_result = self._health_cache
            if now - cached_time < self._health_cache_seconds:
                return cached_result
        try:
            resp = await self._http.get("/health", timeout=10.0)
            healthy = resp.status_code == 200
        except Exception:
            healthy = False
        self._health_cache = (now, healthy)
        return healthy

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST JSON to a Crawl4AI endpoint and return parsed response."""
        try:
            resp = await self._http.post(path, json=payload)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise Crawl4AIClientError(f"Crawl4AI {path} timed out: {exc}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise Crawl4AIClientError(
                f"Crawl4AI {path} returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:200]}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise Crawl4AIClientError(
                f"Crawl4AI {path} connection failed: {exc}", retryable=True
            ) from exc
        try:
            return resp.json()
        except ValueError:
            return resp.text


# ------------------------------------------------------------------
# Camoufox client
# ------------------------------------------------------------------


class CamoufoxClientError(RuntimeError):
    """Raised when a Camoufox sidecar call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class CamoufoxClient:
    """HTTP client for the VPS Camoufox stealth-Firefox sidecar (POST /content -> raw HTML)."""

    _MAX_HTML_BYTES = 8 * 1024 * 1024  # 8 MiB raw-HTML cap

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        health_cache_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._health_cache_seconds = health_cache_seconds
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        )
        self._health_cache: tuple[float, bool] | None = None

    async def fetch_html(self, url: str, *, max_bytes: int | None = None) -> str:
        """POST /content -> raw HTML string.

        Retries once on HTTP 503 (cold-start browser init) after a 2s backoff.
        """
        payload = {"url": url, "gotoOptions": {"waitUntil": "networkidle", "timeout": 15000}}
        for attempt in range(1, 4):
            try:
                resp = await self._http.post("/content", json=payload)
            except httpx.TimeoutException as exc:
                raise CamoufoxClientError(f"Camoufox timed out: {exc}", retryable=True) from exc
            except httpx.RequestError as exc:
                raise CamoufoxClientError(
                    f"Camoufox connection failed: {exc}", retryable=True
                ) from exc
            if resp.status_code == 503:
                if attempt < 3:
                    await asyncio.sleep(2.0**attempt)
                    continue
                raise CamoufoxClientError("Camoufox 503 after 3 retries", retryable=True)
            if resp.status_code != 200:
                raise CamoufoxClientError(
                    f"Camoufox returned HTTP {resp.status_code}",
                    retryable=resp.status_code >= 500,
                )
            if not resp.headers.get("content-type", "").startswith("text/html"):
                raise CamoufoxClientError(
                    "Camoufox returned non-HTML content type", retryable=False
                )
            body = resp.text
            if not body.strip():
                raise CamoufoxClientError("Camoufox returned empty body", retryable=True)
            max_body_bytes = max_bytes or self._MAX_HTML_BYTES
            if len(body.encode("utf-8")) > max_body_bytes:
                message = (
                    "Camoufox response exceeds 8 MiB cap"
                    if max_bytes is None
                    else f"Camoufox response exceeds {max_body_bytes} byte cap"
                )
                raise CamoufoxClientError(message, retryable=False)
            return body
        raise CamoufoxClientError("Camoufox request failed after 3 attempts", retryable=True)

    async def health_check(self) -> bool:
        """GET /health — process-alive only (does NOT warm the browser)."""
        now = time.monotonic()
        if self._health_cache is not None:
            cached_time, cached_result = self._health_cache
            if now - cached_time < self._health_cache_seconds:
                return cached_result
        try:
            resp = await self._http.get("/health", timeout=10.0)
            healthy = resp.status_code == 200
        except Exception:
            healthy = False
        self._health_cache = (now, healthy)
        return healthy

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ------------------------------------------------------------------
# Module-level singletons

_client: Crawl4AIClient | None = None
_camoufox_client: CamoufoxClient | None = None


def get_crawl4ai_client() -> Crawl4AIClient | None:
    """Get or create the singleton Crawl4AI client. Returns None if CRAWL4AI_BASE_URL is not set."""
    global _client
    from ..settings import settings

    if not settings.crawl4ai_base_url:
        return None
    if _client is None:
        _client = Crawl4AIClient(
            settings.crawl4ai_base_url,
            timeout=settings.crawl4ai_timeout_seconds,
            health_cache_seconds=settings.crawl4ai_health_cache_seconds,
        )
        LOGGER.info(
            "Crawl4AI client initialized: %s (timeout=%ss)",
            settings.crawl4ai_base_url,
            settings.crawl4ai_timeout_seconds,
        )
    return _client


async def close_crawl4ai_client() -> None:
    """Cleanup the singleton Crawl4AI client on shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_camoufox_client() -> CamoufoxClient | None:
    """Get or create the singleton Camoufox client. Returns None if CAMOUFOX_BASE_URL is not set."""
    global _camoufox_client
    from ..settings import settings

    if not settings.camoufox_base_url:
        return None
    if _camoufox_client is None:
        _camoufox_client = CamoufoxClient(
            settings.camoufox_base_url,
            timeout=settings.camoufox_timeout_seconds,
            health_cache_seconds=settings.camoufox_health_cache_seconds,
        )
        LOGGER.info(
            "Camoufox client initialized: %s (timeout=%ss)",
            settings.camoufox_base_url,
            settings.camoufox_timeout_seconds,
        )
    return _camoufox_client


async def close_camoufox_client() -> None:
    """Cleanup the singleton Camoufox client on shutdown."""
    global _camoufox_client
    if _camoufox_client is not None:
        await _camoufox_client.close()
        _camoufox_client = None


# ------------------------------------------------------------------
# Apify client
# ------------------------------------------------------------------


class ApifyClientError(RuntimeError):
    """Raised when an Apify run-sync call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ApifyClient:
    """Thin httpx client for the Apify run-sync-get-dataset-items endpoint.

    A single POST starts the Actor, waits for completion (sync ceiling ~5 min)
    and returns the default dataset items — no polling machinery required.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 90.0,
        base_url: str = "https://api.apify.com",
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def run_sync_get_dataset_items(
        self, actor: str, run_input: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """POST /v2/actors/{actor}/run-sync-get-dataset-items → dataset items."""
        from ..settings import settings

        merged = dict(run_input)
        extra = getattr(settings, "apify_extra_input_json", None)
        if isinstance(extra, dict):
            merged.update(extra)

        path = f"/v2/actors/{actor}/run-sync-get-dataset-items"
        try:
            resp = await self._http.post(path, json=merged)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ApifyClientError(f"Apify {path} timed out: {exc}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 402, 403):
                raise ApifyClientError(
                    f"Apify {path} returned HTTP {status}: token/credits issue "
                    "(check APIFY_API_TOKEN or prepaid usage).",
                    retryable=False,
                ) from exc
            raise ApifyClientError(
                f"Apify {path} returned HTTP {status}: {exc.response.text[:200]}",
                retryable=status >= 500 or status == 429,
            ) from exc
        except httpx.RequestError as exc:
            raise ApifyClientError(f"Apify {path} connection failed: {exc}", retryable=True) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ApifyClientError("Apify returned a non-JSON body", retryable=True) from exc
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        if not isinstance(data, list) or not data:
            raise ApifyClientError(
                "Apify run finished but returned no dataset items", retryable=False
            )
        return [item for item in data if isinstance(item, dict)]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


_apify_client: ApifyClient | None = None


def get_apify_client() -> ApifyClient | None:
    """Get or create the singleton Apify client. Returns None if APIFY_API_TOKEN is not set."""
    global _apify_client
    from ..settings import settings

    if not settings.apify_api_token:
        return None
    if _apify_client is None:
        _apify_client = ApifyClient(
            settings.apify_api_token, timeout=settings.apify_timeout_seconds
        )
        LOGGER.info("Apify client initialized (timeout=%ss)", settings.apify_timeout_seconds)
    return _apify_client


async def close_apify_client() -> None:
    """Cleanup the singleton Apify client on shutdown."""
    global _apify_client
    if _apify_client is not None:
        await _apify_client.close()
        _apify_client = None
