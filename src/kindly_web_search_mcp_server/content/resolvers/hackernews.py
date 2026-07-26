from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..sanitize import sanitize_markdown


class HackerNewsError(RuntimeError):
    pass


@dataclass(frozen=True)
class HackerNewsTarget:
    item_id: int


def parse_hackernews_url(url: str) -> HackerNewsTarget:
    """Parse a HackerNews URL: https://news.ycombinator.com/item?id=<number> or https://hn.algolia.com/api/v1/items/<number>"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"news.ycombinator.com", "www.news.ycombinator.com", "hn.algolia.com"}:
        raise HackerNewsError(f"Unsupported HackerNews host: {host or '(missing)'}")

    item_id: int | None = None

    if "algolia.com" in host:
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[-1].isdigit():
            item_id = int(parts[-1])
    else:
        qs = parse_qs(parsed.query)
        id_list = qs.get("id")
        if id_list and id_list[0].isdigit():
            item_id = int(id_list[0])

    if item_id is None:
        raise HackerNewsError("URL does not contain a valid HackerNews item ID.")

    return HackerNewsTarget(item_id=item_id)


def _clean_hn_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    # Convert <p> tags to newlines, strip remaining tags
    text = raw_html.replace("<p>", "\n\n").replace("</p>", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return sanitize_markdown(text).strip()


def _render_hn_comments(
    children: list[dict[str, Any]], depth: int = 0, max_depth: int = 4
) -> list[str]:
    lines: list[str] = []
    if depth > max_depth or not children:
        return lines

    indent = "  " * depth
    for c in children:
        if not isinstance(c, dict):
            continue
        author = str(c.get("author") or c.get("by") or "anonymous").strip()
        created = str(c.get("created_at") or "").strip()
        text = _clean_hn_html(str(c.get("text") or ""))

        if not text:
            continue

        header_prefix = "#" * min(depth + 3, 6)
        meta = f"by @{author}"
        if created:
            meta += f" | {created}"

        lines.append(f"{header_prefix} {indent}Comment {meta}".strip())
        for text_line in text.split("\n"):
            lines.append(f"{indent}{text_line}".rstrip())
        lines.append("")

        sub_children = c.get("children") or []
        if isinstance(sub_children, list) and sub_children:
            lines.extend(_render_hn_comments(sub_children, depth=depth + 1, max_depth=max_depth))

    return lines


def render_hackernews_markdown(*, item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("story_title") or "HackerNews Item").strip()
    url = str(item.get("url") or item.get("story_url") or "").strip()
    points = item.get("points") or item.get("score") or 0
    author = str(item.get("author") or item.get("by") or "anonymous").strip()
    created_at = str(item.get("created_at") or "").strip()
    text = _clean_hn_html(str(item.get("text") or ""))

    lines: list[str] = [f"# HackerNews: {title}"]
    meta_parts = []
    if url:
        meta_parts.append(f"Link: {url}")
    meta_parts.append(f"Points: {points}")
    meta_parts.append(f"Author: @{author}")
    if created_at:
        meta_parts.append(f"Date: {created_at}")

    lines.append(" | ".join(meta_parts))
    lines.append("")

    if text:
        lines.append(text)
        lines.append("")

    children = item.get("children") or []
    if isinstance(children, list) and children:
        lines.append("## Discussion")
        lines.extend(_render_hn_comments(children, depth=0))

    return "\n".join(lines).strip() + "\n"


async def fetch_hackernews_thread_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    target = parse_hackernews_url(url)

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search/1.0"}

        # Primary API: Algolia HN Item API
        algolia_url = f"https://hn.algolia.com/api/v1/items/{target.item_id}"
        try:
            resp = await client.get(algolia_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("id"):
                    return render_hackernews_markdown(item=data)
        except Exception:
            pass  # Fallback to Firebase API

        # Secondary API: Firebase HN API
        firebase_url = f"https://hacker-news.firebaseio.com/v0/item/{target.item_id}.json"
        resp = await client.get(firebase_url, headers=headers)
        if resp.status_code != 200 or not resp.json():
            raise HackerNewsError(
                f"HackerNews item not found or deleted (HTTP {resp.status_code})."
            )

        fb_data = resp.json()
        return render_hackernews_markdown(item=fb_data)

    if http_client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
