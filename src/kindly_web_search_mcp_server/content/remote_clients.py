"""Remote HTTP clients for content rendering.

Crawl4AI ``POST /crawl`` (agent-ready Markdown), Camoufox ``POST /content``
(stealth-Firefox HTML), and Bright Data Web Unlocker ``POST /request``
(Markdown past anti-bot walls).

Singleton pattern: one client instance per backend, lazy-initialized from settings.

Crawl4AI usage::

    client = get_crawl4ai_client()
    if client is not None:
        items = await client.crawl(
            ["https://example.com"],
            browser_params=crawl4ai_browser_params(),
            crawler_params=crawl4ai_markdown_params(),
        )

Camoufox usage::

    client = get_camoufox_client()
    if client is not None:
        page = await client.fetch_html("https://example.com")

Web Unlocker usage::

    client = get_unlocker_client()
    if client is not None:
        result = await client.fetch_markdown("https://example.com")
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
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


class Crawl4AIConfigError(Crawl4AIClientError):
    """Raised when the Crawl4AI endpoint is not configured."""


def extract_crawl_markdown_candidates(item: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return ordered fit/raw Markdown variants from one Crawl4AI result."""
    markdown = item.get("markdown")
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(markdown, Mapping):
        values = (
            ("fit_markdown", "fit"),
            ("fit", "fit"),
            ("raw_markdown", "raw"),
            ("raw", "raw"),
        )
        for key, variant in values:
            value = markdown.get(key)
            if isinstance(value, str) and value.strip() and variant not in seen:
                candidates.append({"variant": variant, "markdown": value})
                seen.add(variant)
    elif isinstance(markdown, str) and markdown.strip():
        candidates.append({"variant": "fit", "markdown": markdown})
    for key, variant in (("fit_markdown", "fit"), ("raw_markdown", "raw")):
        value = item.get(key)
        if isinstance(value, str) and value.strip() and variant not in seen:
            candidates.append({"variant": variant, "markdown": value})
            seen.add(variant)
    return candidates


def crawl4ai_browser_params() -> dict[str, object]:
    """BrowserConfig params for Markdown crawls.

    ``text_mode`` / ``light_mode`` skip image and font downloads that never
    appear in Markdown. Real Chrome + patchright come from the server config.
    """
    return {
        "headless": True,
        "verbose": False,
        "text_mode": True,
        "light_mode": True,
    }


def crawl4ai_markdown_params(
    *,
    target_elements: list[str] | None = None,
    css_selector: str | None = None,
    wait_for: str | None = None,
    javascript: list[str] | None = None,
    javascript_before_wait: list[str] | None = None,
    scan_full_page: bool | None = None,
) -> dict[str, object]:
    """CrawlerRunConfig params that emit agent-ready fit Markdown.

    An explicit PruningContentFilter is required: without it the server
    returns ``fit_markdown=None``. Threshold 0.3 / fixed / min 0 keeps code
    fences while dropping share/nav chrome (live A/B 2026-09-15).
    ``target_elements`` is omitted unless the caller knows the page has that
    container — sending ``article`` on a docs shell yields empty fit Markdown.
    """
    params: dict[str, object] = {
        "cache_mode": "bypass",
        "word_count_threshold": 2,
        "remove_overlay_elements": True,
        "remove_consent_popups": True,
        "excluded_tags": ["nav", "header", "footer", "aside", "form"],
        "exclude_external_links": False,
        "exclude_social_media_domains": [],
        "excluded_selector": "div[class*='share'], .post-nav, .sidebar",
        "markdown_generator": {
            "type": "DefaultMarkdownGenerator",
            "params": {
                "options": {
                    "ignore_links": False,
                    "body_width": 0,
                    "escape_html": False,
                    "skip_internal_links": False,
                },
                "content_filter": {
                    "type": "PruningContentFilter",
                    "params": {
                        "threshold": 0.3,
                        "threshold_type": "fixed",
                        "min_word_threshold": 0,
                    },
                },
            },
        },
    }
    if target_elements:
        params["target_elements"] = list(target_elements)
    if css_selector:
        params["css_selector"] = css_selector
    if wait_for:
        params["wait_for"] = wait_for
    if javascript_before_wait:
        params["js_code_before_wait"] = list(javascript_before_wait)
    if javascript:
        params["js_code"] = list(javascript)
    if scan_full_page is not None:
        params["scan_full_page"] = scan_full_page
    return params


class Crawl4AIClient:
    """HTTP client for the trusted Crawl4AI ``/crawl`` endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        health_cache_seconds: float = 30.0,
        token: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._health_cache_seconds = health_cache_seconds
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers=headers,
        )
        self._health_cache: tuple[float, bool] | None = None
        self._capability_cache: tuple[float, dict[str, Any]] | None = None

    async def crawl(
        self,
        urls: list[str],
        *,
        browser_params: Mapping[str, Any],
        crawler_params: Mapping[str, Any],
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """POST /crawl with typed BrowserConfig and CrawlerRunConfig wrappers."""
        payload = {
            "urls": list(urls),
            "browser_config": {
                "type": "BrowserConfig",
                "params": dict(browser_params),
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": dict(crawler_params),
            },
        }
        data = await self._post_json("/crawl", payload, timeout=timeout)
        if isinstance(data, list):
            if not all(isinstance(item, Mapping) for item in data):
                raise Crawl4AIClientError(
                    "Crawl4AI /crawl returned a non-object result", retryable=False
                )
            return [dict(item) for item in data]
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                if not all(isinstance(item, Mapping) for item in results):
                    raise Crawl4AIClientError(
                        "Crawl4AI /crawl returned a non-object result",
                        retryable=False,
                    )
                return [dict(item) for item in results]
            if data.get("success") is False:
                message = str(data.get("error") or data.get("message") or "Crawl4AI crawl failed")
                return [{"url": url, "success": False, "error": message} for url in urls]
        raise Crawl4AIClientError("Crawl4AI /crawl returned an invalid response", retryable=False)

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
        except (httpx.RequestError, OSError):
            healthy = False
        self._health_cache = (now, healthy)
        return healthy

    async def capability_summary(self) -> dict[str, Any]:
        """Return compact health/schema evidence for detailed crawl responses."""
        now = time.monotonic()
        if self._capability_cache is not None:
            cached_time, cached_summary = self._capability_cache
            if now - cached_time < self._health_cache_seconds:
                return cached_summary
        diagnostics: list[dict[str, str]] = []
        summary: dict[str, Any] = {}
        try:
            health = await self._get_json("/health")
        except Crawl4AIClientError as exc:
            summary["health"] = {"available": False}
            diagnostics.append({"endpoint": "/health", "message": str(exc)[:200]})
        else:
            summary["health"] = {"available": True, **_compact_capability_data(health)}
        try:
            schema = await self._get_json("/schema")
        except Crawl4AIClientError as exc:
            summary["schema"] = {"available": False}
            diagnostics.append({"endpoint": "/schema", "message": str(exc)[:200]})
        else:
            summary["schema"] = {"available": True, **_compact_capability_data(schema)}
        if diagnostics:
            summary["diagnostics"] = diagnostics
        self._capability_cache = (now, summary)
        return summary

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _get_json(self, path: str) -> Any:
        """GET JSON from a Crawl4AI endpoint."""
        try:
            resp = await self._http.get(path)
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
                f"Crawl4AI {path} connection failed: {exc}",
                retryable=True,
            ) from exc
        try:
            return resp.json()
        except ValueError as exc:
            raise Crawl4AIClientError(
                f"Crawl4AI {path} returned non-JSON data", retryable=False
            ) from exc

    async def _post_json(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> Any:
        """POST JSON to a Crawl4AI endpoint and return parsed response."""
        try:
            resp = await self._http.post(path, json=payload, timeout=timeout)
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
                f"Crawl4AI {path} connection failed: {exc}",
                retryable=True,
            ) from exc
        try:
            return resp.json()
        except ValueError:
            return resp.text


def _compact_capability_data(value: Any) -> dict[str, Any]:
    """Keep health/schema payloads bounded and free of raw server documents."""
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key in ("status", "version", "service", "name"):
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)):
                compact[key] = item
        if not compact:
            compact["keys"] = sorted(str(key) for key in value)[:32]
        return compact
    if isinstance(value, list):
        return {"item_count": len(value)}
    if isinstance(value, (str, int, float, bool)):
        return {"value": value}
    return {}


# ------------------------------------------------------------------
# Camoufox client
# ------------------------------------------------------------------


class CamoufoxClientError(RuntimeError):
    """Raised when a Camoufox sidecar call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class CamoufoxResult:
    """HTML body and origin metadata from one Camoufox ``/content`` call."""

    html: str
    http_status: int | None
    fetched_url: str | None
    response_headers: dict[str, str]


_CAMOUFOX_STATUS_HEADERS = (
    "x-origin-status",
    "x-status-code",
    "x-http-status",
    "x-response-status",
)


def _camoufox_origin_status(headers: httpx.Headers) -> int | None:
    """Read origin HTTP status from sidecar response headers when present."""
    for name in _CAMOUFOX_STATUS_HEADERS:
        raw = headers.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 100 <= value <= 599:
            return value
    return None


def _camoufox_parse_body(resp: httpx.Response) -> tuple[str, int | None, str | None]:
    """Extract HTML, optional origin status, and optional fetched URL."""
    origin_status = _camoufox_origin_status(resp.headers)
    content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    fetched_url: str | None = None
    if content_type in {"application/json", "text/json"}:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CamoufoxClientError("Camoufox returned invalid JSON", retryable=False) from exc
        if not isinstance(payload, Mapping):
            raise CamoufoxClientError("Camoufox JSON body was not an object", retryable=False)
        html_value = payload.get("html") or payload.get("content") or payload.get("body")
        if not isinstance(html_value, str) or not html_value.strip():
            raise CamoufoxClientError("Camoufox JSON body had no HTML", retryable=True)
        status_value = (
            payload.get("status") or payload.get("statusCode") or payload.get("status_code")
        )
        if (
            origin_status is None
            and isinstance(status_value, int)
            and not isinstance(status_value, bool)
        ):
            origin_status = status_value if 100 <= status_value <= 599 else None
        url_value = payload.get("url") or payload.get("fetched_url") or payload.get("finalUrl")
        fetched_url = url_value if isinstance(url_value, str) else None
        return html_value, origin_status, fetched_url
    if content_type not in {"", "text/html", "application/xhtml+xml"}:
        raise CamoufoxClientError(
            f"Camoufox returned unsupported content type {content_type!r}", retryable=False
        )
    html = resp.text
    if not html.strip():
        raise CamoufoxClientError("Camoufox returned empty body", retryable=True)
    return html, origin_status, fetched_url


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

    async def fetch_html(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        timeout: float | None = None,
    ) -> CamoufoxResult:
        """POST /content and return HTML plus origin status when the sidecar sends it.

        Retries on HTTP 502/503 (cold-start browser init / transient gateway
        errors) with exponential backoff, up to 3 attempts. Origin status is
        unset when the sidecar only returns HTML — never forged as 200.
        """
        payload = {"url": url, "gotoOptions": {"waitUntil": "networkidle", "timeout": 15000}}
        for attempt in range(1, 4):
            try:
                resp = await self._http.post("/content", json=payload, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise CamoufoxClientError(f"Camoufox timed out: {exc}", retryable=True) from exc
            except httpx.RequestError as exc:
                raise CamoufoxClientError(
                    f"Camoufox connection failed: {exc}", retryable=True
                ) from exc
            if resp.status_code in (502, 503):
                if attempt < 3:
                    await asyncio.sleep(2.0**attempt)
                    continue
                raise CamoufoxClientError("Camoufox 502/503 after 3 retries", retryable=True)
            if resp.status_code != 200:
                raise CamoufoxClientError(
                    f"Camoufox returned HTTP {resp.status_code}",
                    retryable=resp.status_code >= 500,
                )
            html, origin_status, fetched_url = _camoufox_parse_body(resp)
            max_body_bytes = max_bytes or self._MAX_HTML_BYTES
            if len(html.encode("utf-8")) > max_body_bytes:
                message = (
                    "Camoufox response exceeds 8 MiB cap"
                    if max_bytes is None
                    else f"Camoufox response exceeds {max_body_bytes} byte cap"
                )
                raise CamoufoxClientError(message, retryable=False)
            return CamoufoxResult(
                html=html,
                http_status=origin_status,
                fetched_url=fetched_url,
                response_headers=dict(resp.headers),
            )
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
        except (httpx.RequestError, OSError):
            healthy = False
        self._health_cache = (now, healthy)
        return healthy

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ------------------------------------------------------------------
# Bright Data Web Unlocker
# ------------------------------------------------------------------

_UNLOCKER_RETRYABLE_CODES = frozenset({"reject_block"})
_UNLOCKER_ENDPOINT = "https://api.brightdata.com/request"


class UnlockerClientError(RuntimeError):
    """Raised when a Web Unlocker call fails."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        error_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_code = error_code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class UnlockerResult:
    """Markdown body returned by a successful Unlocker request."""

    markdown: str
    http_status: int | None
    response_headers: dict[str, str]


def _unlocker_retryable(error_code: str | None) -> bool:
    """True when a later peer is worth one retry."""
    if error_code is None:
        return False
    return error_code in _UNLOCKER_RETRYABLE_CODES or error_code.startswith("resolve_failed_")


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    """Parse an optional integer response header."""
    raw = headers.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class UnlockerClient:
    """HTTP client for Bright Data Web Unlocker ``POST /request``."""

    def __init__(self, api_key: str, zone: str, *, timeout: float = 90.0) -> None:
        self._zone = zone
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def fetch_markdown(self, url: str, *, timeout: float | None = None) -> UnlockerResult:
        """Return page Markdown, retrying once on peer-level unlock failures."""
        payload = {
            "zone": self._zone,
            "url": url,
            "format": "raw",
            "data_format": "markdown",
        }
        last_error: UnlockerClientError | None = None
        for attempt in range(2):
            try:
                return await self._request_markdown(payload, timeout=timeout)
            except UnlockerClientError as exc:
                if not exc.retryable or attempt == 1:
                    raise
                last_error = exc
                await asyncio.sleep(1.0)
        raise UnlockerClientError(
            "Web Unlocker failed after retry", retryable=False
        ) from last_error

    async def _request_markdown(
        self, payload: dict[str, str], *, timeout: float | None = None
    ) -> UnlockerResult:
        """POST one Unlocker request and classify the response."""
        try:
            resp = await self._http.post(_UNLOCKER_ENDPOINT, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise UnlockerClientError(f"Web Unlocker timed out: {exc}", retryable=False) from exc
        except httpx.RequestError as exc:
            raise UnlockerClientError(
                f"Web Unlocker connection failed: {exc}", retryable=True
            ) from exc

        error_code = resp.headers.get("x-brd-error-code") or resp.headers.get("x-brd-err-code")
        error_message = resp.headers.get("x-brd-error") or resp.headers.get("x-brd-err-msg")
        origin_status = _header_int(resp.headers, "x-brd-status-code")
        if resp.status_code in {400, 401, 403, 407}:
            detail = (resp.text or error_message or "").strip()[:300]
            raise UnlockerClientError(
                f"Web Unlocker returned HTTP {resp.status_code}: {detail}",
                retryable=False,
                error_code=error_code,
                http_status=resp.status_code,
            )
        if error_code:
            message = error_message or error_code
            raise UnlockerClientError(
                f"Web Unlocker failed ({error_code!r}): {message}",
                retryable=_unlocker_retryable(error_code),
                error_code=error_code,
                http_status=origin_status or resp.status_code,
            )
        if resp.status_code == 429:
            raise UnlockerClientError(
                f"Web Unlocker returned HTTP 429: {(error_message or resp.text)[:200]}",
                retryable=False,
                error_code="sr_rate_limit",
                http_status=429,
            )
        if resp.status_code >= 500:
            raise UnlockerClientError(
                f"Web Unlocker returned HTTP {resp.status_code}",
                retryable=True,
                http_status=resp.status_code,
            )
        markdown = resp.text
        if not isinstance(markdown, str) or not markdown.strip():
            raise UnlockerClientError(
                "Web Unlocker returned empty content",
                retryable=False,
                http_status=origin_status or resp.status_code,
            )
        return UnlockerResult(
            markdown=markdown,
            http_status=origin_status if origin_status is not None else 200,
            response_headers=dict(resp.headers),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ------------------------------------------------------------------
# Module-level singletons

_client: Crawl4AIClient | None = None
_unlocker_client_LOCK = threading.RLock()
_unlocker_client: UnlockerClient | None = None
_client_LOCK = threading.RLock()
_camoufox_client_LOCK = threading.RLock()
_camoufox_client: CamoufoxClient | None = None


def get_crawl4ai_client() -> Crawl4AIClient | None:
    """Get or create the singleton Crawl4AI client. Returns None if CRAWL4AI_BASE_URL is not set."""
    global _client
    from ..settings import settings

    if not settings.crawl4ai_base_url:
        return None
    if _client is None:
        with _client_LOCK:
            if _client is None:
                _client = Crawl4AIClient(
                    settings.crawl4ai_base_url,
                    timeout=settings.crawl4ai_timeout_seconds,
                    health_cache_seconds=settings.crawl4ai_health_cache_seconds,
                    token=settings.crawl4ai_token,
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
        with _camoufox_client_LOCK:
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


def get_unlocker_client() -> UnlockerClient | None:
    """Get or create the singleton Unlocker client.

    Returns None unless both ``BRIGHTDATA_API_KEY`` and
    ``BRIGHTDATA_UNLOCKER_ZONE`` are set.
    """
    global _unlocker_client
    from ..settings import settings

    api_key = settings.brightdata_api_key.strip()
    zone = settings.brightdata_unlocker_zone.strip()
    if not api_key or not zone:
        return None
    if _unlocker_client is None:
        with _unlocker_client_LOCK:
            if _unlocker_client is None:
                _unlocker_client = UnlockerClient(
                    api_key,
                    zone,
                    timeout=settings.brightdata_unlocker_timeout_seconds,
                )
                LOGGER.info(
                    "Web Unlocker client initialized (zone=%s timeout=%ss)",
                    zone,
                    settings.brightdata_unlocker_timeout_seconds,
                )
    return _unlocker_client


async def close_unlocker_client() -> None:
    """Cleanup the singleton Unlocker client on shutdown."""
    global _unlocker_client
    if _unlocker_client is not None:
        await _unlocker_client.close()
        _unlocker_client = None


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
            raise ApifyClientError(
                f"Apify {path} connection failed: {exc}", retryable=True
            ) from exc

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


_apify_client_LOCK = threading.RLock()

_apify_client: ApifyClient | None = None


def get_apify_client() -> ApifyClient | None:
    """Get or create the singleton Apify client. Returns None if APIFY_API_TOKEN is not set."""
    global _apify_client
    from ..settings import settings

    if not settings.apify_api_token:
        return None
    if _apify_client is None:
        with _apify_client_LOCK:
            if _apify_client is None:
                _apify_client = ApifyClient(
                    settings.apify_api_token, timeout=settings.apify_timeout_seconds
                )
                LOGGER.info(
                    "Apify client initialized (timeout=%ss)", settings.apify_timeout_seconds
                )
    return _apify_client
