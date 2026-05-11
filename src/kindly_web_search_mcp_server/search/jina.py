"""Jina AI Search API provider - returns Markdown format."""
from __future__ import annotations

import os
import re

import httpx

from ..models import WebSearchResult
from ..retry import retry_with_backoff


class JinaError(RuntimeError):
    pass


class JinaConfigError(JinaError):
    pass


def _get_jina_api_key() -> str:
    api_key = os.environ.get("JINA_API_KEY", "").strip()
    if not api_key:
        raise JinaConfigError(
            "JINA_API_KEY is not set. Configure it as an environment variable."
        )
    return api_key


def _parse_jina_markdown(text: str) -> list[dict[str, str]]:
    """Parse Jina's Markdown response format.

    Format per result:
    [N] Title: ...
    [N] URL Source: ...
    [N] Description: ...
    (optional content)
    [N] Published Time: ...
    """
    results: dict[int, dict[str, str]] = {}

    # Extract all title lines: [N] Title: ...
    for match in re.finditer(r'\[(\d+)\] Title:\s*(.+)', text):
        idx = int(match.group(1))
        if idx not in results:
            results[idx] = {}
        results[idx]['title'] = match.group(2).strip()

    # Extract all URL lines: [N] URL Source: ...
    for match in re.finditer(r'\[(\d+)\] URL Source:\s*(.+)', text):
        idx = int(match.group(1))
        if idx not in results:
            results[idx] = {}
        results[idx]['url'] = match.group(2).strip()

    # Extract all description lines: [N] Description: ...
    for match in re.finditer(r'\[(\d+)\] Description:\s*(.+)', text):
        idx = int(match.group(1))
        if idx not in results:
            results[idx] = {}
        results[idx]['description'] = match.group(2).strip()

    # Convert to list, sorted by index, filter incomplete
    output: list[dict[str, str]] = []
    for idx in sorted(results.keys()):
        item = results[idx]
        if 'title' in item and 'url' in item:
            output.append(item)

    return output


async def search_jina(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Query Jina AI Search API and return parsed results.

    Jina endpoint:
    - GET https://s.jina.ai/{query}
    - Header: Authorization: Bearer <JINA_API_KEY>
    - Returns Markdown text with numbered sections

    Docs: https://jina.ai/search-api
    """
    if not query.strip():
        return []

    if num_results < 1:
        return []

    api_key = _get_jina_api_key()
    url = f"https://s.jina.ai/{query}"
    headers = {"Authorization": f"Bearer {api_key}"}

    async def _do_request(client: httpx.AsyncClient) -> str:
        resp = await client.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text

    if http_client is None:
        async with httpx.AsyncClient(timeout=30) as client:
            async def _request() -> str:
                return await _do_request(client)
            raw_text = await retry_with_backoff(
                _request,
                provider_name="jina",
                max_retries=2,
            )
    else:
        async def _request_with_client() -> str:
            return await _do_request(http_client)
        raw_text = await retry_with_backoff(
            _request_with_client,
            provider_name="jina",
            max_retries=2,
        )

    # Parse Markdown response
    parsed = _parse_jina_markdown(raw_text)

    results: list[WebSearchResult] = []
    for item in parsed:
        title = item.get('title', '')
        link = item.get('url', '')
        snippet = item.get('description', '')

        if not title or not link:
            continue

        results.append(WebSearchResult(title=title, link=link, snippet=snippet))
        if len(results) >= num_results:
            break

    return results
