from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..sanitize import sanitize_markdown


class RedditError(RuntimeError):
    pass


@dataclass(frozen=True)
class RedditTarget:
    subreddit: str
    post_id: str


_REDDIT_PATH_RE = re.compile(r"^/r/([^/]+)/comments/([^/]+)")
_REDD_IT_RE = re.compile(r"^/([^/]+)")


def parse_reddit_url(url: str) -> RedditTarget:
    """Parse a Reddit post URL: https://www.reddit.com/r/<subdir>/comments/<id>/... or https://redd.it/<id>"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host == "redd.it":
        m = _REDD_IT_RE.match(parsed.path or "")
        if m and m.group(1):
            return RedditTarget(subreddit="auto", post_id=m.group(1))
        raise RedditError("Invalid redd.it short URL format.")

    if host not in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
        raise RedditError(f"Unsupported Reddit host: {host or '(missing)'}")

    path = parsed.path or ""
    m = _REDDIT_PATH_RE.match(path)
    if not m:
        raise RedditError("URL is not a recognized Reddit post URL.")

    subreddit, post_id = m.group(1), m.group(2)
    return RedditTarget(subreddit=subreddit, post_id=post_id)


def _render_reddit_comments(
    children: list[dict[str, Any]], depth: int = 0, max_depth: int = 3
) -> list[str]:
    lines: list[str] = []
    if depth > max_depth or not children:
        return lines

    indent = "  " * depth
    for item in children:
        if not isinstance(item, dict) or item.get("kind") != "t1":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue

        body = str(data.get("body") or "").strip()
        if not body or body in {"[deleted]", "[removed]"}:
            continue

        author = str(data.get("author") or "anonymous").strip()
        score = data.get("score", 0)

        header_prefix = "#" * min(depth + 3, 6)
        lines.append(f"{header_prefix} {indent}Comment by u/{author} (Score: {score})".strip())

        sanitized_body = sanitize_markdown(body)
        for text_line in sanitized_body.split("\n"):
            lines.append(f"{indent}{text_line}".rstrip())
        lines.append("")

        replies = data.get("replies")
        if isinstance(replies, dict):
            rep_data = replies.get("data")
            if isinstance(rep_data, dict):
                rep_children = rep_data.get("children")
                if isinstance(rep_children, list) and rep_children:
                    lines.extend(
                        _render_reddit_comments(rep_children, depth=depth + 1, max_depth=max_depth)
                    )

    return lines


def render_reddit_markdown(
    *, post_data: dict[str, Any], comments_data: list[dict[str, Any]]
) -> str:
    title = str(post_data.get("title") or "Reddit Post").strip()
    subreddit = str(post_data.get("subreddit") or "").strip()
    author = str(post_data.get("author") or "anonymous").strip()
    score = post_data.get("score", 0)
    upvote_ratio = post_data.get("upvote_ratio")
    num_comments = post_data.get("num_comments", 0)
    selftext = str(post_data.get("selftext") or "").strip()
    url = str(post_data.get("url") or "").strip()
    permalink = str(post_data.get("permalink") or "").strip()

    lines: list[str] = [f"# Reddit: {title} (r/{subreddit})"]
    meta_parts = []
    if permalink:
        full_url = f"https://www.reddit.com{permalink}"
        meta_parts.append(f"Link: {full_url}")
    elif url:
        meta_parts.append(f"Link: {url}")

    meta_parts.append(f"Author: u/{author}")
    meta_parts.append(f"Score: {score}")
    if upvote_ratio is not None:
        try:
            meta_parts.append(f"Upvoted: {int(float(upvote_ratio) * 100)}%")
        except Exception:
            pass
    meta_parts.append(f"Comments: {num_comments}")

    lines.append(" | ".join(meta_parts))
    lines.append("")

    if selftext and selftext not in {"[deleted]", "[removed]"}:
        lines.append(sanitize_markdown(selftext).strip())
        lines.append("")

    if comments_data:
        lines.append("## Top Comments")
        lines.extend(_render_reddit_comments(comments_data, depth=0))

    return "\n".join(lines).strip() + "\n"


async def fetch_reddit_thread_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    target = parse_reddit_url(url)

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) kindly-web-search/1.0"}
        json_url = f"https://www.reddit.com/r/{target.subreddit}/comments/{target.post_id}.json?limit=50&sort=confidence"
        if target.subreddit == "auto":
            json_url = (
                f"https://www.reddit.com/comments/{target.post_id}.json?limit=50&sort=confidence"
            )

        resp = await client.get(json_url, headers=headers)
        if resp.status_code != 200:
            raise RedditError(f"Reddit post inaccessible or private (HTTP {resp.status_code}).")

        data = resp.json()
        if not isinstance(data, list) or len(data) < 2:
            raise RedditError("Invalid JSON structure returned by Reddit API.")

        post_listing = data[0].get("data", {}).get("children", [])
        if not post_listing or not isinstance(post_listing[0], dict):
            raise RedditError("Reddit post data empty or missing.")

        post_data = post_listing[0].get("data", {})
        comments_data = data[1].get("data", {}).get("children", [])

        return render_reddit_markdown(post_data=post_data, comments_data=comments_data)

    if http_client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
