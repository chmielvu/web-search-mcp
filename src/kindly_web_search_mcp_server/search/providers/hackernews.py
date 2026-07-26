"""HackerNews search provider via Algolia Search API.

No API key required. Rate limit: 10,000 requests/hour.
API docs: https://hn.algolia.com/api
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import run_provider

logger = logging.getLogger(__name__)

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HN_DISCUSSION_MARKERS = (
    "discussion",
    "comment",
    "comments",
    "thread",
    "debate",
    "thoughts",
    "opinion",
    "compare",
    "comparison",
    "what do you think",
    "ask hn",
    "show hn",
)


class HackerNewsError(RuntimeError):
    pass


def _should_search_comments(query: str) -> bool:
    normalized = query.casefold()
    return any(marker in normalized for marker in _HN_DISCUSSION_MARKERS)


def _short_date(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:10]


def _format_story_title(hit: dict[str, Any]) -> str:
    title = hit.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    story_title = hit.get("story_title")
    if isinstance(story_title, str) and story_title.strip():
        return story_title.strip()

    comment_text = hit.get("comment_text")
    if isinstance(comment_text, str) and comment_text.strip():
        return comment_text.strip().splitlines()[0][:120]

    return "Hacker News result"


def _format_story_link(hit: dict[str, Any]) -> str | None:
    url = hit.get("url") or hit.get("story_url")
    if isinstance(url, str) and url.strip():
        return url.strip()

    object_id = hit.get("objectID") or hit.get("story_id")
    if isinstance(object_id, str) and object_id.strip():
        return f"https://news.ycombinator.com/item?id={object_id.strip()}"
    return None


def _format_story_snippet(hit: dict[str, Any], *, story_hit: bool) -> str:
    author = hit.get("author")
    author_text = author.strip() if isinstance(author, str) and author.strip() else "(deleted)"
    points = hit.get("points")
    num_comments = hit.get("num_comments")
    created = _short_date(hit.get("created_at"))
    parts: list[str] = []
    if story_hit:
        if isinstance(points, (int, float)):
            parts.append(f"{int(points)} pts")
        if isinstance(num_comments, (int, float)):
            parts.append(f"{int(num_comments)} comments")
    else:
        story_title = hit.get("story_title")
        if isinstance(story_title, str) and story_title.strip():
            parts.append(f"comment on {story_title.strip()}")
    parts.append(f"by {author_text}")
    if created:
        parts.append(created)
    return " | ".join(parts)


def _parse_hit(hit: dict[str, Any], *, story_hit: bool) -> WebSearchResult | None:
    title = _format_story_title(hit)
    link = _format_story_link(hit)
    if not link:
        return None
    snippet = _format_story_snippet(hit, story_hit=story_hit)
    if not story_hit:
        story_title = hit.get("story_title")
        if isinstance(story_title, str) and story_title.strip():
            title = f"Comment on {story_title.strip()}"
    return WebSearchResult(title=title, link=link, snippet=snippet)


def _selected_tags(query: str) -> list[str]:
    tags = ["story"]
    if _should_search_comments(query):
        tags.append("comment")
    return tags


async def search_hackernews(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search HackerNews stories and comments via Algolia API.

    Uses Algolia's relevance-ranked endpoint. When a query looks
    discussion-oriented, both stories and comments are searched and the
    results are merged with de-duplication.
    """
    if not query.strip() or num_results < 1:
        return []

    tags = _selected_tags(query)

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        responses = await asyncio.gather(
            *[
                client.get(
                    _HN_SEARCH_URL,
                    params={
                        "query": query,
                        "tags": tag,
                        "hitsPerPage": num_results,
                    },
                )
                for tag in tags
            ],
            return_exceptions=True,
        )

        for response in responses:
            if isinstance(response, asyncio.CancelledError):
                raise response

        payloads: list[tuple[str, dict[str, Any]]] = []
        failures: list[str] = []
        for tag, response in zip(tags, responses, strict=False):
            if isinstance(response, Exception):
                failures.append(tag)
                logger.warning("Hacker News %s search failed for tag=%s: %s", query, tag, response)
                continue
            try:
                response.raise_for_status()  # type: ignore[union-attr]
            except Exception as exc:
                failures.append(tag)
                logger.warning(
                    "Hacker News %s search returned an HTTP error for tag=%s: %s",
                    query,
                    tag,
                    exc,
                )
                continue
            try:
                data = response.json()  # type: ignore[union-attr]
            except ValueError as exc:
                failures.append(tag)
                logger.warning(
                    "Hacker News %s search returned invalid JSON for tag=%s: %s",
                    query,
                    tag,
                    exc,
                )
                continue
            if not isinstance(data, dict):
                failures.append(tag)
                logger.warning(
                    "Hacker News %s search returned invalid payload for tag=%s",
                    query,
                    tag,
                )
                continue
            payloads.append((tag, data))

        if not payloads:
            raise HackerNewsError(f"Hacker News searches failed for query {query!r}")

        if failures:
            logger.warning(
                "Hacker News returned partial results for query=%r after %d tag failure(s)",
                query,
                len(failures),
            )

        return {"payloads": payloads}

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        payloads = data.get("payloads")
        if not isinstance(payloads, list):
            return []

        results: list[WebSearchResult] = []
        seen_links: set[str] = set()
        for payload_entry in payloads:
            if not isinstance(payload_entry, tuple) or len(payload_entry) != 2:
                continue
            tag, payload = payload_entry
            if not isinstance(tag, str) or not isinstance(payload, dict):
                continue
            hits = payload.get("hits")
            if not isinstance(hits, list):
                continue

            story_hit = tag == "story"
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                parsed = _parse_hit(hit, story_hit=story_hit)
                if parsed is None or parsed.link in seen_links:
                    continue
                seen_links.add(parsed.link)
                results.append(parsed)
                if len(results) >= num_results:
                    return results

        return results

    return await run_provider(
        "hackernews",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
