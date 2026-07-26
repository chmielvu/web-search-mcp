"""Reddit search provider via OAuth API or public search fallback.

Supports REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET for OAuth (100 req/min).
Falls back to public search endpoint with ratelimit header parsing and post body extraction.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import run_provider

logger = logging.getLogger(__name__)

_USER_AGENT = "desktop:com.kindly.web-search-mcp:v1.0 (by /u/research_bot)"
_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_SEARCH_URL = "https://oauth.reddit.com/search"
_PUBLIC_SEARCH_URL = "https://www.reddit.com/search.json"

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}
_token_lock = asyncio.Lock()


def _reddit_delay_seconds() -> float:
    raw = str(getattr(settings, "reddit_delay_seconds", 2.0)).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.0


async def _get_oauth_token(client: httpx.AsyncClient) -> str | None:
    client_id_raw = getattr(settings, "reddit_client_id", "")
    client_secret_raw = getattr(settings, "reddit_client_secret", "")
    client_id = client_id_raw.strip() if isinstance(client_id_raw, str) else ""
    client_secret = client_secret_raw.strip() if isinstance(client_secret_raw, str) else ""
    if not client_id or not client_secret:
        return None

    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    async with _token_lock:
        if _token_cache["token"] and now < _token_cache["expires_at"]:
            return _token_cache["token"]

        try:
            auth_str = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers = {
                "Authorization": f"Basic {auth_str}",
                "User-Agent": _USER_AGENT,
            }
            resp = await client.post(
                _OAUTH_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers=headers,
                timeout=settings.search_retrieve_budget_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            if isinstance(token, str) and token:
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + max(60.0, float(expires_in) - 300.0)
                return token
        except Exception as exc:
            logger.warning("Failed to obtain Reddit OAuth access token: %s", exc)

    return None


async def search_reddit(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search Reddit globally or across relevant subreddits with body extraction."""
    if not query.strip() or num_results < 1:
        return []

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        token = await _get_oauth_token(client)
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

        if token:
            url = _OAUTH_SEARCH_URL
            headers["Authorization"] = f"Bearer {token}"
            params: dict[str, Any] = {
                "q": query,
                "limit": min(num_results, 50),
                "sort": "relevance",
                "t": "all",
            }
        else:
            url = _PUBLIC_SEARCH_URL
            params = {
                "q": query,
                "limit": min(num_results, 50),
                "sort": "relevance",
                "t": "all",
            }
            await asyncio.sleep(_reddit_delay_seconds())

        resp = await client.get(url, params=params, headers=headers)

        # Proactive Rate-Limit Delay
        try:
            response_headers = getattr(resp, "headers", {})
            remaining_raw = response_headers.get("x-ratelimit-remaining")
            reset_raw = response_headers.get("x-ratelimit-reset")
            if remaining_raw is not None and reset_raw is not None:
                remaining = float(remaining_raw)
                reset = float(reset_raw)
                if remaining < 5.0 and reset > 0:
                    await asyncio.sleep(min(reset, 10.0))
        except (ValueError, TypeError):
            pass

        status_code = getattr(resp, "status_code", 200)
        if status_code in (403, 429):
            logger.warning("Reddit search returned rate limit / anti-bot status: %s", status_code)
            return {}

        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        outer_data = data.get("data")
        if not isinstance(outer_data, dict):
            return []

        children = outer_data.get("children", [])
        if not isinstance(children, list):
            return []

        results: list[WebSearchResult] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            child_data = child.get("data")
            if not isinstance(child_data, dict):
                continue

            title = child_data.get("title")
            if not isinstance(title, str) or not title.strip():
                continue

            permalink = child_data.get("permalink")
            if isinstance(permalink, str) and permalink.strip():
                link = f"https://www.reddit.com{permalink.strip()}"
            else:
                link = child_data.get("url_overridden_by_dest") or child_data.get("url")
                if not isinstance(link, str) or not link.strip():
                    continue

            subreddit = child_data.get("subreddit", "unknown")
            score = child_data.get("score", 0)
            num_comments = child_data.get("num_comments", 0)
            selftext = child_data.get("selftext", "")
            clean_selftext = (
                selftext.strip()[:350] if isinstance(selftext, str) and selftext.strip() else ""
            )

            snippet_parts = [f"r/{subreddit}", f"{score} pts", f"{num_comments} comments"]
            metadata = " | ".join(snippet_parts)
            snippet = f"{metadata}\n{clean_selftext}" if clean_selftext else metadata

            results.append(WebSearchResult(title=title.strip(), link=link, snippet=snippet))
            if len(results) >= num_results:
                break

        return results

    return await run_provider(
        "reddit",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
