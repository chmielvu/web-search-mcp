"""Reusable Bright Data Web Scraper API client (structured records, not page fetch).

The search SERP adapter (``search/providers/brightdata.py``) and the Web
Unlocker fetch stage use ``POST /request`` with zones. This client covers the
other Bright Data surface: ``POST /datasets/v3/scrape`` (sync, <= 20 URLs,
1-minute ceiling, else HTTP 202 + snapshot_id) for pre-built scrapers such
as the YouTube "Collect videos by URL" dataset.

Docs:
https://docs.brightdata.com/products/scrapers/youtube/introduction
https://docs.brightdata.com/api-reference/scrapers/synchronous-requests
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SCRAPER_ENDPOINT = "https://api.brightdata.com/datasets/v3/scrape"

# Auth/rate-limit/zone errors are never retried automatically; everything
# else at 5xx or 429 with no Retry-After context is caller-retryable.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class ScraperClientError(RuntimeError):
    """Raised when a Web Scraper API call fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _parse_scrape_records(resp: httpx.Response) -> list[dict[str, Any]]:
    """Extract the record list from a 200 /datasets/v3/scrape response."""
    try:
        data = resp.json()
    except ValueError as exc:
        raise ScraperClientError("Scraper API returned a non-JSON body", retryable=True) from exc
    if isinstance(data, dict) and isinstance(data.get("snapshot_id"), str):
        raise ScraperClientError(
            f"Scraper API deferred to snapshot {data['snapshot_id']} "
            "(sync ceiling exceeded; use the async trigger workflow)",
            retryable=False,
        )
    if not isinstance(data, list):
        raise ScraperClientError(
            f"Scraper API returned an unexpected body: {type(data).__name__}",
            retryable=False,
        )
    return [item for item in data if isinstance(item, dict)]


class BrightDataScraperClient:
    """HTTP client for Bright Data Web Scraper API ``POST /datasets/v3/scrape``."""

    def __init__(self, api_key: str, *, timeout: float = 90.0) -> None:
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def scrape_records(
        self,
        dataset_id: str,
        inputs: list[dict[str, Any]],
        *,
        include_errors: bool = True,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """POST one sync scrape and return the record list (HTTP 200 only)."""
        params = {"dataset_id": dataset_id, "format": "json"}
        if include_errors:
            params["include_errors"] = "true"
        body = {"input": inputs}
        try:
            resp = await self._http.post(
                _SCRAPER_ENDPOINT, params=params, json=body, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise ScraperClientError(f"Scraper API timed out: {exc}", retryable=True) from exc
        except httpx.RequestError as exc:
            raise ScraperClientError(
                f"Scraper API connection failed: {exc}", retryable=True
            ) from exc
        if resp.status_code in (400, 401, 403):
            raise ScraperClientError(
                f"Scraper API returned HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=False,
            )
        if resp.status_code == 429:
            raise ScraperClientError(
                f"Scraper API returned HTTP 429: {resp.text[:200]}", retryable=True
            )
        if resp.status_code == 202:
            try:
                snapshot_id = resp.json().get("snapshot_id", "?")
            except ValueError:
                snapshot_id = "?"
            raise ScraperClientError(
                f"Scraper API deferred to snapshot {snapshot_id} "
                "(sync ceiling exceeded; use the async trigger workflow)",
                retryable=False,
            )
        if resp.status_code >= 500 or resp.status_code not in (200,):
            raise ScraperClientError(
                f"Scraper API returned HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code in _RETRYABLE_STATUS_CODES,
            )
        return _parse_scrape_records(resp)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


_scraper_client_LOCK = threading.RLock()
_scraper_client: BrightDataScraperClient | None = None


def get_brightdata_scraper_client() -> BrightDataScraperClient | None:
    """Get or create the singleton Scraper API client.

    Returns None when BRIGHTDATA_API_KEY is not set.
    """
    global _scraper_client
    from ..settings import settings

    if not settings.brightdata_api_key.strip():
        return None
    if _scraper_client is None:
        with _scraper_client_LOCK:
            if _scraper_client is None:
                _scraper_client = BrightDataScraperClient(
                    settings.brightdata_api_key.strip(),
                    timeout=settings.brightdata_scraper_timeout_seconds,
                )
                logger.info(
                    "Bright Data scraper client initialized (timeout=%ss)",
                    settings.brightdata_scraper_timeout_seconds,
                )
    return _scraper_client


async def close_brightdata_scraper_client() -> None:
    """Cleanup the singleton Scraper API client on shutdown."""
    global _scraper_client
    if _scraper_client is not None:
        await _scraper_client.close()
        _scraper_client = None
