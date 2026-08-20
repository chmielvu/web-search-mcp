"""Specialized resolver for Discourse forum topics (https://<forum>/t/<slug>/<id>).

Fetches structured topic metadata and posts directly from the Discourse JSON API.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..extract import extract_content_as_markdown
from ..sanitize import sanitize_markdown


class DiscourseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscourseTarget:
    base_url: str
    topic_id: str
    slug: str | None


_DISCOURSE_TOPIC_RE = re.compile(r"^/t/(?:([^/]+)/)?(\d+)(?:/.*)?$")


def parse_discourse_url(url: str) -> DiscourseTarget | None:
    """Parse a Discourse forum topic URL (e.g. https://meta.discourse.org/t/slug/123 or https://forum.example.com/t/123)."""
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or ""
        m = _DISCOURSE_TOPIC_RE.match(path)
        if not m:
            return None
        slug = m.group(1)
        topic_id = m.group(2)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        return DiscourseTarget(base_url=base_url, topic_id=topic_id, slug=slug)
    except Exception:
        return None


def render_discourse_markdown(data: dict[str, Any], url: str) -> str:
    """Render Discourse topic JSON to structured Markdown."""
    title = data.get("title") or "Discourse Topic"
    views = data.get("views", 0)
    posts_count = data.get("posts_count", 0)
    like_count = data.get("like_count", 0)
    created_at = (data.get("created_at") or "")[:10]
    tags = data.get("tags") or []

    lines: list[str] = [
        f"# {title}",
        f"**Source:** {url}",
    ]

    meta_parts: list[str] = []
    if created_at:
        meta_parts.append(f"**Date:** {created_at}")
    if views:
        meta_parts.append(f"**Views:** {views:,}")
    if posts_count:
        meta_parts.append(f"**Posts:** {posts_count}")
    if like_count:
        meta_parts.append(f"**Likes:** {like_count}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    if tags:
        lines.append("\n**Tags:** " + ", ".join(f"`{t}`" for t in tags[:8]))

    post_stream = data.get("post_stream", {})
    posts = post_stream.get("posts", [])

    if posts:
        # Original post
        op = posts[0]
        op_author = op.get("username") or op.get("name") or "author"
        op_body = op.get("raw") or ""
        if not op_body and op.get("cooked"):
            op_body = extract_content_as_markdown(op["cooked"])

        lines.append("\n## Original Post")
        lines.append(f"**Author:** @{op_author}\n")
        lines.append(sanitize_markdown(op_body.strip()))
        lines.append("")

        # Replies
        replies = posts[1:]
        if replies:
            lines.append("## Replies\n")
            for p in replies[:20]:
                author = p.get("username") or p.get("name") or "anonymous"
                post_num = p.get("post_number", "")
                p_likes = p.get("score", 0)
                accepted = " [Accepted Answer]" if p.get("accepted_answer") else ""

                p_body = p.get("raw") or ""
                if not p_body and p.get("cooked"):
                    p_body = extract_content_as_markdown(p["cooked"])

                lines.append(f"### #{post_num} by @{author}{accepted} (Score: {p_likes})")
                lines.append(sanitize_markdown(p_body.strip()))
                lines.append("")

            if len(replies) > 20:
                lines.append(f"_... and {len(replies) - 20} more replies_")

    return "\n".join(lines).strip() + "\n"


async def fetch_discourse_topic_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch Discourse topic JSON and return clean Markdown."""
    target = parse_discourse_url(url)
    if not target:
        raise DiscourseError(f"URL is not a recognized Discourse topic URL: {url}")

    api_url = f"{target.base_url}/t/{target.topic_id}.json"

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kindly-web-search/1.0",
            "Accept": "application/json",
        }
        resp = await client.get(api_url, headers=headers)
        if resp.status_code == 404:
            raise DiscourseError(f"Discourse topic '{target.topic_id}' not found (404).")
        if resp.status_code != 200:
            raise DiscourseError(f"Discourse API returned HTTP {resp.status_code}")
        data = resp.json()
        return render_discourse_markdown(data, url)

    if http_client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
