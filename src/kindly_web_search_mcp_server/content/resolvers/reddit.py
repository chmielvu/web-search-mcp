"""Specialized resolver for Reddit threads with multi-layer resilience.

Resolves Reddit threads through:
1. Direct Reddit JSON API (via curl_cffi with browser impersonation or httpx)
2. old.reddit.com HTML extraction
3. Arctic Shift / Pullpush public Reddit archive API
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..sanitize import sanitize_markdown

LOGGER = logging.getLogger(__name__)


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


async def _fetch_reddit_direct_json(target: RedditTarget) -> str:
    """Fetch Reddit JSON via curl_cffi or httpx."""
    json_url = f"https://www.reddit.com/r/{target.subreddit}/comments/{target.post_id}.json?limit=50&sort=confidence"
    if target.subreddit == "auto":
        json_url = f"https://www.reddit.com/comments/{target.post_id}.json?limit=50&sort=confidence"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }

    try:
        from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found,import-untyped]

        async with AsyncSession(impersonate="chrome124", follow_redirects=True) as session:
            resp = await session.get(json_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) >= 2:
                    post_listing = data[0].get("data", {}).get("children", [])
                    if post_listing and isinstance(post_listing[0], dict):
                        post_data = post_listing[0].get("data", {})
                        comments_data = data[1].get("data", {}).get("children", [])
                        return render_reddit_markdown(
                            post_data=post_data, comments_data=comments_data
                        )
    except Exception as exc:
        LOGGER.debug("curl_cffi direct Reddit JSON failed: %s", exc)

    # httpx fallback
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(json_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) >= 2:
                post_listing = data[0].get("data", {}).get("children", [])
                if post_listing and isinstance(post_listing[0], dict):
                    post_data = post_listing[0].get("data", {})
                    comments_data = data[1].get("data", {}).get("children", [])
                    return render_reddit_markdown(post_data=post_data, comments_data=comments_data)
        raise RedditError(f"Direct Reddit JSON returned HTTP {resp.status_code}")


def _parse_old_reddit_html(html: str, target: RedditTarget) -> str:
    """Extract post and comments from old.reddit.com HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title_elem = soup.find("a", class_="title")
    title = title_elem.get_text().strip() if title_elem else "Reddit Post"

    author_elem = soup.find("a", class_="author")
    author = author_elem.get_text().strip() if author_elem else "anonymous"

    score_elem = soup.find("div", class_="score unvoted") or soup.find("span", class_="score")
    score = score_elem.get_text().strip() if score_elem else "0"

    selftext_elem = soup.find("div", class_="usertext-body")
    selftext = selftext_elem.get_text().strip() if selftext_elem else ""

    lines = [
        f"# Reddit: {title} (r/{target.subreddit})",
        f"**Author:** u/{author} | **Score:** {score} | **Link:** https://www.reddit.com/r/{target.subreddit}/comments/{target.post_id}/",
        "",
    ]
    if selftext:
        lines.append(sanitize_markdown(selftext))
        lines.append("")

    comments = soup.find_all("div", class_="comment")
    if comments:
        lines.append("## Top Comments")
        for c in comments[:20]:
            c_author_elem = c.find("a", class_="author")
            c_author = c_author_elem.get_text().strip() if c_author_elem else "anonymous"
            c_body_elem = c.find("div", class_="usertext-body")
            c_body = c_body_elem.get_text().strip() if c_body_elem else ""
            if c_body and c_body not in ("[deleted]", "[removed]"):
                lines.append(f"### Comment by u/{c_author}")
                lines.append(sanitize_markdown(c_body))
                lines.append("")

    return "\n".join(lines).strip() + "\n"


async def _fetch_old_reddit_html(target: RedditTarget) -> str:
    """Fetch and parse old.reddit.com via curl_cffi."""
    old_url = f"https://old.reddit.com/r/{target.subreddit}/comments/{target.post_id}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found,import-untyped]

        async with AsyncSession(impersonate="chrome124", follow_redirects=True) as session:
            resp = await session.get(old_url, headers=headers, timeout=12)
            if resp.status_code == 200 and resp.text:
                return _parse_old_reddit_html(resp.text, target)
    except Exception as exc:
        LOGGER.debug("old.reddit curl_cffi fetch failed: %s", exc)

    raise RedditError("old.reddit HTML fetch failed")


async def _fetch_reddit_arctic_shift(target: RedditTarget) -> str:
    """Fetch Reddit thread data from Arctic Shift public API."""
    api_url = f"https://arctic-shift.photon-reddit.com/api/posts/ids?ids={target.post_id}"
    headers = {"User-Agent": "kindly-web-search-mcp/1.0 (reddit-fallback)"}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(api_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            posts = data.get("data", [])
            if posts and isinstance(posts[0], dict):
                p = posts[0]
                post_data = {
                    "title": p.get("title"),
                    "subreddit": p.get("subreddit") or target.subreddit,
                    "author": p.get("author"),
                    "score": p.get("score", 0),
                    "upvote_ratio": p.get("upvote_ratio"),
                    "num_comments": p.get("num_comments", 0),
                    "selftext": p.get("selftext", ""),
                    "permalink": p.get("permalink", ""),
                }
                return render_reddit_markdown(post_data=post_data, comments_data=[])
    raise RedditError("Arctic Shift API fetch failed")


async def fetch_reddit_thread_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch Reddit thread markdown with multi-layer fallback cascade."""
    target = parse_reddit_url(url)

    # Layer 1: Direct Reddit JSON API
    try:
        return await _fetch_reddit_direct_json(target)
    except Exception as exc:
        LOGGER.debug("Reddit Layer 1 direct JSON failed for %s: %s", url, exc)

    # Layer 2: old.reddit.com with curl_cffi
    try:
        return await _fetch_old_reddit_html(target)
    except Exception as exc:
        LOGGER.debug("Reddit Layer 2 old.reddit failed for %s: %s", url, exc)

    # Layer 3: Arctic Shift public archive
    try:
        return await _fetch_reddit_arctic_shift(target)
    except Exception as exc:
        LOGGER.debug("Reddit Layer 3 Arctic Shift failed for %s: %s", url, exc)

    raise RedditError(f"All specialized Reddit resolution layers failed for {url}")
