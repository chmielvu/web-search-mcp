"""Free-tier SERP provider using Gemma 4 31B with Google Search grounding.

Uses `tools: [{"googleSearch": {}}]` per official Gemma REST API docs
(ai.google.dev/gemma/docs/core/gemma_on_gemini_api) to get real web-grounded
results with groundingMetadata containing source URLs and domains.

REST API uses camelCase: googleSearch, thinkingConfig, safetySettings.
The snake_case `google_search` / `google_search_retrieval` are SDK-level
abstractions — not valid in raw REST payloads.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import run_clientless_provider

logger = logging.getLogger(__name__)

MODEL = "gemma-4-26b-a4b-it"

_PROMPT = (
    "Search the web for: {query}\n\n"
    "List the top results. For each, output:\n"
    "URL: full_URL\n"
    "TITLE: page_title\n"
    "SNIPPET: One sentence describing what the page contains.\n\n"
    "Output only the results. No intro text."
)


def _parse_presentation_text(text: str) -> list[dict[str, str]]:
    """Parse the model's formatted output into structured results."""
    results: list[dict[str, str]] = []
    blocks = re.split(r"\n(?=\d+\.\s|\*\*|\* |URL:)", text)

    for block in blocks:
        url_match = None
        title_match = None
        snippet_match = None

        url_match = re.search(r"URL:\s*(https?://[^\s\n)]+)", block)
        title_match = re.search(r"(?:TITLE|Title|##\s*)\s*:\s*(.+?)(?:\n|$)", block)
        snippet_match = re.search(r"(?:SNIPPET|Snippet|Description)\s*:\s*(.+?)(?:\n|$)", block)

        # Also try numbered list: 1. **Title** — URL: ...
        if not url_match:
            url_match = re.search(r"(?:URL|Link|url|link)\s*[=:]\s*(https?://[^\s\n)]+)", block)
        if not url_match and not title_match:
            md_link = re.search(r"\*\*([^*]+)\*\*.*?\((https?://[^)]+)\)", block)
            md_title = re.search(r"\*\*([^*]+)\*\*", block)
            if md_link:
                results.append(
                    {
                        "url": md_link.group(2),
                        "title": md_link.group(1),
                        "snippet": block.strip().split("\n", 1)[-1][:200],
                    }
                )
                continue
            if md_title:
                plain_url = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", block)
                if plain_url:
                    results.append(
                        {
                            "url": plain_url.group(2),
                            "title": plain_url.group(1),
                            "snippet": block.strip().split("\n", 1)[-1][:200],
                        }
                    )
                    continue

        if not url_match:
            continue

        url = url_match.group(1).rstrip("/.,;:)\"'")
        title = title_match.group(1).strip() if title_match else ""
        snippet = snippet_match.group(1).strip() if snippet_match else ""

        if url and title:
            results.append({"url": url, "title": title, "snippet": snippet})

    return results


def _extract_from_grounding(data: dict) -> list[dict[str, str]]:
    """Extract source info from groundingMetadata."""
    results: list[dict[str, str]] = []
    candidate = data.get("candidates", [{}])[0]
    grounding = candidate.get("groundingMetadata") or {}
    chunks = grounding.get("groundingChunks", [])
    supports = grounding.get("groundingSupports", [])

    # Build map of segment text for snippets
    segment_map: dict[int, str] = {}
    for sup in supports:
        for idx in sup.get("groundingChunkIndices", []):
            seg = sup.get("segment", {})
            segment_map[idx] = seg.get("text", "")

    seen_domains = set()
    for chunk in chunks:
        web = chunk.get("web", {})
        uri = web.get("uri", "")
        domain = web.get("title", "")
        if not uri:
            continue
        if domain and domain in seen_domains:
            continue
        seen_domains.add(domain)
        results.append(
            {
                "url": uri,
                "title": domain,
                "snippet": "",
            }
        )

    return results


def _configured_api_key() -> str:
    """Return the configured Google AI API key (first/only). Use the first one."""
    return settings.gemini_api_key.strip()


async def _grounded_search(prompt: str) -> dict:
    """Call Gemma with googleSearch grounding. One model, one key, global timeout."""
    api_key = _configured_api_key()
    if not api_key:
        return {}

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.1},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
        f":generateContent?key={api_key}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.search_retrieve_budget_seconds),
        ) as client:
            resp = await client.post(url, json=payload)
        return resp.json()
    except Exception as exc:
        logger.debug("Gemma grounding failed: %s", exc)
        return {}


async def search_gemma(
    query: str,
    *,
    num_results: int,
    options: Any = None,
    arguments: dict[str, Any] | None = None,
    http_client: Any = None,
    query_embedding: Any = None,
) -> list[WebSearchResult]:
    """Free-tier SERP provider using Gemini 2.5 Flash with Google Search grounding.

    Args:
        query: Search query
        num_results: Target number of results
        http_client: Ignored

    Returns:
        list[WebSearchResult]
    """
    if not query.strip():
        return []

    prompt = _PROMPT.format(query=query[:500])

    async def _request() -> list[dict[str, str]]:
        data = await _grounded_search(prompt)
        if not data:
            return []

        model_used = data.get("modelVersion", MODEL)
        # Try parsing the model's presentation text first (has titles + URLs + snippets)
        text = ""
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if not part.get("thought", False):
                text += part.get("text", "")

        results = _parse_presentation_text(text)
        if results:
            for r in results:
                r["_model_used"] = model_used
            return results[:num_results]

        # Fallback: use grounding metadata for domains
        fallback = _extract_from_grounding(data)
        for r in fallback:
            r["_model_used"] = model_used
        return fallback[:num_results]

    def _parse_response(raw_results: list[dict[str, str]]) -> list[WebSearchResult]:
        results: list[WebSearchResult] = []
        for item in raw_results:
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            if not url or not title:
                continue
            domain = urlparse(url).netloc or url
            results.append(
                WebSearchResult(
                    title=title,
                    link=url,
                    snippet=snippet,
                    domain=domain,
                    diagnostics=[
                        {
                            "source": "gemini_grounded_serp",
                            "model": item.get("_model_used", MODEL),
                            "provider": "google_search_grounding",
                        }
                    ],
                )
            )
        return results

    return await run_clientless_provider(
        "gemma",
        query,
        num_results,
        request=_request,
        parse_response=_parse_response,
    )
