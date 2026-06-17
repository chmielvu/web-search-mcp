"""Remote HTTP client for Crawl4AI Docker server.

Provides a thin async wrapper around the Crawl4AI Docker REST API endpoints:
  - POST /md     — clean markdown extraction (fit/raw/bm25/llm filters)
  - POST /crawl  — full crawl returning markdown + html + links per URL
  - GET  /health — server health check

Singleton pattern: one client instance, lazy-initialized from settings.crawl4ai_base_url.

Usage::

    client = get_crawl4ai_client()
    if client is not None:
        results = await client.crawl("https://example.com")
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


class Crawl4AIClientError(RuntimeError):
    """Raised when a Crawl4AI remote call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Crawl4AIClient:
    """HTTP client for remote Crawl4AI Docker server.

    Parameters
    ----------
    base_url : str
        Crawl4AI server URL, e.g. ``http://vps-ip:11235``.
    timeout : float
        Default request timeout in seconds.
    health_cache_seconds : float
        How long to cache the health check result.
    """

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

    # ------------------------------------------------------------------
    # Core API methods
    # ------------------------------------------------------------------

    async def fetch_markdown(
        self,
        url: str,
        *,
        mode: str = "fit",
        query: str | None = None,
    ) -> str:
        """POST /md — clean markdown extraction.

        Parameters
        ----------
        url : str
            Page URL to extract.
        mode : str
            Filter mode: ``fit`` (PruningContentFilter), ``bm25`` (query-scored),
            ``raw`` (full page), ``llm`` (LLM-filtered).
        query : str | None
            Query string for ``bm25`` mode.

        Returns
        -------
        str
            Extracted markdown text.

        Raises
        ------
        Crawl4AIClientError
            On HTTP errors or empty responses.
        """
        payload: dict[str, Any] = {"url": url, "f": mode, "c": "0"}
        if query and mode == "bm25":
            payload["q"] = query

        data = await self._post_json("/md", payload)
        # /md returns the markdown directly as a string in the response
        markdown = data if isinstance(data, str) else str(data)
        if not markdown.strip():
            raise Crawl4AIClientError("Crawl4AI /md returned empty content")
        return markdown

    async def crawl(
        self,
        urls: str | list[str],
        *,
        cache_mode: str = "bypass",
    ) -> list[dict[str, Any]]:
        """POST /crawl — full crawl returning markdown + html + links per URL.

        Parameters
        ----------
        urls : str | list[str]
            Single URL or list of URLs to crawl.
        cache_mode : str
            Cache mode: ``bypass`` (always fresh), ``use`` (use cache).

        Returns
        -------
        list[dict]
            List of crawl results. Each contains: ``url``, ``success``,
            ``markdown`` (dict with ``fit_markdown``, ``raw_markdown``),
            ``html``, ``cleaned_html``, ``links`` (dict with ``internal``,
            ``external``), ``status_code``, ``error_message``.
        """
        if isinstance(urls, str):
            urls = [urls]

        payload = {
            "urls": urls,
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": cache_mode,
                    "verbose": False,
                },
            },
        }

        data = await self._post_json("/crawl", payload)
        # Response format: {"results": [...]}
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            raise Crawl4AIClientError("Crawl4AI /crawl returned no results")
        return results

    async def deep_crawl(
        self,
        url: str,
        *,
        max_depth: int = 3,
        max_pages: int = 50,
        keywords: list[str] | None = None,
        cache_mode: str = "bypass",
    ) -> list[dict[str, Any]]:
        """POST /crawl with BestFirstCrawlingStrategy.

        Discovers and crawls pages in one operation, prioritizing pages
        by keyword relevance (when keywords provided) or path depth.

        Parameters
        ----------
        url : str
            Seed URL to start crawling from.
        max_depth : int
            Maximum link-following depth.
        max_pages : int
            Maximum number of pages to crawl.
        keywords : list[str] | None
            Keywords for URL scoring. When provided, uses
            KeywordRelevanceScorer to prioritize relevant pages.
        cache_mode : str
            Cache mode.

        Returns
        -------
        list[dict]
            List of crawl results (same format as crawl()).
        """
        # Build scorer config
        url_scorer = None
        if keywords:
            url_scorer = {
                "type": "KeywordRelevanceScorer",
                "params": {"keywords": keywords, "weight": 0.7},
            }
        else:
            url_scorer = {
                "type": "PathDepthScorer",
                "params": {"weight": 0.5, "optimal_depth": 3},
            }

        deep_crawl_strategy: dict[str, Any] = {
            "type": "BestFirstCrawlingStrategy",
            "params": {
                "max_depth": max_depth,
                "max_pages": max_pages,
                "include_external": False,
            },
        }
        if url_scorer:
            deep_crawl_strategy["params"]["url_scorer"] = url_scorer

        payload = {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": cache_mode,
                    "verbose": False,
                    "deep_crawl_strategy": deep_crawl_strategy,
                },
            },
        }

        data = await self._post_json("/crawl", payload)
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            raise Crawl4AIClientError("Crawl4AI deep crawl returned no results")
        return results

    async def health_check(self) -> bool:
        """GET /health — check VPS availability.

        Result is cached for ``health_cache_seconds`` to avoid per-request pings.

        Returns
        -------
        bool
            True if server is healthy, False otherwise.
        """
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
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST JSON to a Crawl4AI endpoint and return parsed response."""
        try:
            resp = await self._http.post(path, json=payload)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise Crawl4AIClientError(
                f"Crawl4AI {path} timed out: {exc}", retryable=True
            ) from exc
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
            # /md endpoint returns plain text, not JSON
            return resp.text


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_client: Crawl4AIClient | None = None


def get_crawl4ai_client() -> Crawl4AIClient | None:
    """Get or create the singleton Crawl4AI client.

    Returns ``None`` if ``CRAWL4AI_BASE_URL`` is not configured.
    """
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
    """Cleanup the singleton client on shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
