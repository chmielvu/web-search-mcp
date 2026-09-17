"""DeGoog search aggregator provider.

DeGoog is a self-hosted search aggregator with transport-based engine routing.
API: POST {DEGOOG_BASE_URL}/api/search  {"query": "...", "engines": ["bing","ddg"]}
Response: {results: [{title, url, snippet, score, sources: [...]}]}
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..types import EngineCall, SearchHit
from .base import ProviderRequestError


class DeGoogError(ProviderRequestError):
    pass


class DeGoogConfigError(DeGoogError):
    pass


LOGGER = logging.getLogger(__name__)


def _get_degoog_base_url() -> str:
    base_url = settings.degoog_base_url.strip()
    if not base_url:
        raise DeGoogConfigError(
            "DEGOOG_BASE_URL is not set. "
            "Configure it as an environment variable pointing to your DeGoog instance."
        )
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise DeGoogConfigError(f"DEGOOG_BASE_URL is not a valid URL: {base_url!r}")
    return base_url.rstrip("/")


def _looks_like_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


async def search_degoog(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> EngineCall:
    """Query a DeGoog instance and return parsed results.

    POST {DEGOOG_BASE_URL}/api/search with JSON body {"query": "..."}.
    DeGoog always returns JSON (no format param needed).
    """
    if not query.strip():
        return EngineCall(adapter="degoog", query=query)
    if num_results < 1:
        return EngineCall(adapter="degoog", query=query)

    base_url = _get_degoog_base_url()
    url = f"{base_url}/api/search"

    body: dict[str, Any] = {"query": query, "type": "web"}
    headers = {"Accept": "application/json"}

    timeout_seconds = settings.search_retrieve_budget_seconds

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.post(url, json=body, headers=headers, timeout=timeout_seconds)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise DeGoogError(f"DeGoog returned HTTP {status}.") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise DeGoogError("DeGoog response was not valid JSON.") from exc

        if not isinstance(data, dict):
            raise DeGoogError("DeGoog response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> EngineCall:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise DeGoogError("DeGoog response missing `results` list.")

        if not raw_results:
            LOGGER.debug("DeGoog returned empty results list for query=%r", query)

        hits: list[SearchHit] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            link = item.get("url")
            snippet = item.get("snippet") or item.get("content")

            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip() or not _looks_like_url(link):
                continue
            if not isinstance(snippet, str) or not snippet.strip():
                continue

            domain = extract_domain_from_url(link)
            if not domain:
                continue

            hits.append(
                SearchHit(
                    title=title,
                    url=link,
                    snippet=snippet,
                    domain=domain,
                    adapter="degoog",
                )
            )
            if len(hits) >= num_results:
                break

        return EngineCall(adapter="degoog", query=query, hits=tuple(hits))

    # Direct call without retry_with_backoff — DeGoog gets one 10s attempt.
    if http_client is not None:
        payload = await _do_request(http_client)
    else:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            payload = await _do_request(client)
    return _parse_response(payload)
