"""Brave Search, Autosuggest, and Spellcheck API helpers."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ...settings import get_env_value, settings
from ...utils.text_clean import clean_query as normalize_query
from ...utils.url_canonicalize import extract_domain_from_url
from ..filters import brave_freshness as window_brave_freshness
from ..options import SearchOptions
from ..types import EngineCall, SearchHit
from .base import ProviderRequestError, run_provider


class BraveError(ProviderRequestError):
    """Base error for Brave provider failures."""


class BraveConfigError(BraveError):
    """Raised when the standard Brave API key is missing."""


BRAVE_LLM_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"

_VALID_FRESHNESS_TOKENS: frozenset[str] = frozenset({"pd", "pw", "pm", "py"})
_BRAVE_FRESHNESS_MAP: dict[str, str] = {
    "day": "pd",
    "week": "pw",
    "month": "pm",
    "year": "py",
}


def _get_brave_api_key() -> str:
    """Return the standard Brave API key or raise. Never falls back to Suggest key."""
    api_key = get_env_value("BRAVE_API_KEY", settings.brave_api_key).strip()
    if not api_key:
        raise BraveConfigError("BRAVE_API_KEY is not set. Configure it in your runtime settings.")
    return api_key


def _brave_headers(api_key: str) -> dict[str, str]:
    """Standard Brave auth/accept headers for all Brave endpoints."""
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }


def _bound_brave_query(query: str) -> str:
    """Cap the outbound query to Brave's 400-char / 50-word limit.

    Provider-local: this never alters the user-visible rewrite query or literal
    syntax sent to the free/keyword/neural branches; it only bounds what Brave
    receives. Autosuggest keeps its own independent 200-char bound elsewhere.
    """
    words = query.split()
    if len(words) > 50:
        query = " ".join(words[:50])
    if len(query) > 400:
        query = query[:400]
    return query


def translate_brave_freshness(value: str | None) -> str | None:
    """Map an intent freshness word to a Brave wire token.

    Passes through already-valid Brave tokens (``pd``/``pw``/``pm``/``py``) and
    custom ``YYYY-MM-DDtoYYYY-MM-DD`` ranges. Raises ``BraveError`` for anything
    else so a bad value is never silently turned into a bogus API parameter.
    """
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in _VALID_FRESHNESS_TOKENS:
        return normalized
    if normalized in _BRAVE_FRESHNESS_MAP:
        return _BRAVE_FRESHNESS_MAP[normalized]
    if "to" in normalized:
        return normalized
    raise BraveError(f"Unsupported Brave freshness value: {value!r}")


async def suggest_brave_queries(
    query: str,
    *,
    count: int = 8,
    country: str = "US",
    lang: str = "en",
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Return Brave Autosuggest queries and rich entity metadata."""
    raw_key = os.environ.get("BRAVE_SUGGEST_API_KEY") or settings.brave_suggest_api_key
    api_key = raw_key.strip() if raw_key else ""
    if not api_key:
        return {"suggestions": [], "entities": []}

    url = "https://api.search.brave.com/res/v1/suggest/search"
    bounded_count = max(1, min(count, 20))
    params = {
        "q": query[:200],
        "count": bounded_count,
        "country": country,
        "lang": lang,
        "rich": "true",
    }
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    async def _with_client(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"suggestions": [], "entities": []}
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            return {"suggestions": [], "entities": []}
        suggestions: list[str] = []
        entities: list[dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            suggestion = item.get("query")
            if isinstance(suggestion, str) and suggestion.strip():
                suggestions.append(normalize_query(suggestion))
            if item.get("is_entity") is True:
                title = item.get("title")
                description = item.get("description")
                if isinstance(title, str) and title.strip():
                    entities.append(
                        {
                            "name": title.strip(),
                            "description": description.strip()
                            if isinstance(description, str)
                            else "",
                        }
                    )
        return {
            "suggestions": list(dict.fromkeys(suggestions))[:bounded_count],
            "entities": entities,
        }

    if http_client is not None:
        return await _with_client(http_client)
    async with httpx.AsyncClient(timeout=settings.search_retrieve_budget_seconds) as client:
        return await _with_client(client)


async def search_brave(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    freshness: str | None = None,
    country: str | None = None,
    search_lang: str | None = None,
    goggles: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> EngineCall:
    """Query Brave LLM Context API and return parsed grounding results.

    Replaces the previous standard Brave Web search with the LLM-optimized
    ``/res/v1/llm/context`` endpoint. Parses ``grounding.generic`` into
    ``SearchHit`` rows (title/url/snippet) and never synthesizes an answer.
    """
    api_key = _get_brave_api_key()
    # Resolved-window/locale fallbacks; explicit provider args keep precedence.
    # Note: filter support on /llm/context is not publicly specified — params
    # are sent best-effort and stamped in request diagnostics upstream.
    if freshness is None and search_options is not None and search_options.temporal is not None:
        freshness = window_brave_freshness(search_options.temporal)
    if country is None and search_options is not None:
        country = search_options.region
    if search_lang is None and search_options is not None and search_options.language:
        search_lang = search_options.language.split("-", 1)[0]
    params: dict[str, Any] = {
        "q": _bound_brave_query(query),
        "count": min(num_results, 20),
        "maximum_number_of_urls": min(num_results, 50),
    }
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    brave_freshness = translate_brave_freshness(freshness)
    if brave_freshness:
        params["freshness"] = brave_freshness
    if goggles:
        params["goggles"] = list(goggles)

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.get(
            BRAVE_LLM_CONTEXT_URL, params=params, headers=_brave_headers(api_key)
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise BraveError("Brave LLM Context response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise BraveError("Brave LLM Context response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> EngineCall:
        grounding = data.get("grounding") if isinstance(data, dict) else None
        generic = grounding.get("generic") if isinstance(grounding, dict) else None
        if not isinstance(generic, list):
            return EngineCall(adapter="brave", query=query)
        hits: list[SearchHit] = []
        for entry in generic:
            if not isinstance(entry, dict):
                continue
            link = entry.get("url") or entry.get("source")
            if not isinstance(link, str) or not link.strip():
                continue
            link = link.strip()
            domain = extract_domain_from_url(link)
            if not domain:
                continue
            title = entry.get("title")
            if not isinstance(title, str) or not title.strip():
                title = link.split("//")[-1].split("/")[0] or link
            snippets_raw = entry.get("snippets")
            if isinstance(snippets_raw, list):
                snippet = " ".join(s for s in snippets_raw if isinstance(s, str))
            else:
                snippet = (
                    entry.get("snippet") or entry.get("content") or entry.get("description") or ""
                )
            if not isinstance(snippet, str):
                snippet = ""
            hits.append(
                SearchHit(
                    title=title,
                    url=link,
                    snippet=snippet.strip(),
                    domain=domain,
                    adapter="brave",
                )
            )
            if len(hits) >= num_results:
                break
        return EngineCall(adapter="brave", query=query, hits=tuple(hits))

    return await run_provider(
        "brave",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
    )
