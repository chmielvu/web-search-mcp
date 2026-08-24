"""Apify-backed resolver for X/Twitter URLs (x.com / twitter.com).

Runs the pinned pay-per-event Actor through the Apify run-sync API and renders
tweets/profile timelines as markdown. The layer is inert unless APIFY_API_TOKEN
is configured; on any failure it raises :class:`TwitterError` so the pipeline
falls through to the generic Tier-2 extraction cascade.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..remote_clients import get_apify_client
from ..sanitize import sanitize_markdown

logger = logging.getLogger(__name__)


class TwitterError(RuntimeError):
    """Invalid/unsupported X/Twitter URL or Apify resolution failure."""


@dataclass(frozen=True)
class TwitterTarget:
    """Parsed X/Twitter URL target."""

    tweet_id: str  # empty string for profile URLs
    screen_name: str | None


_TWITTER_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
_STATUS_PATH_RE = re.compile(r"^/(?:i/web/)?[^/]+/status/(\d+)")
_STATUS_ONLY_RE = re.compile(r"^/(?:i/web/)?status/(\d+)")
_PROFILE_PATH_RE = re.compile(r"^/([A-Za-z0-9_]{1,15})/?$")
_RESERVED_SEGMENTS = {
    "i",
    "home",
    "explore",
    "search",
    "notifications",
    "messages",
    "settings",
    "intent",
}


def parse_twitter_url(url: str) -> TwitterTarget:
    """Parse an x.com/twitter.com status or profile URL into a target."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _TWITTER_HOSTS:
        raise TwitterError(f"Unsupported X/Twitter host: {host or '(missing)'}")

    path = parsed.path or "/"
    m = _STATUS_PATH_RE.match(path) or _STATUS_ONLY_RE.match(path)
    if m:
        return TwitterTarget(tweet_id=m.group(1), screen_name=None)

    m = _PROFILE_PATH_RE.match(path)
    if m and m.group(1).lower() not in _RESERVED_SEGMENTS:
        return TwitterTarget(tweet_id="", screen_name=m.group(1))

    raise TwitterError("URL is not a recognized X/Twitter status or profile URL.")


def _pick(item: dict[str, Any], *names: str) -> Any:
    """Return the first non-empty alias value from an Actor item."""
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def _render_tweet_item(item: dict[str, Any]) -> str | None:
    """Render one Actor item as tweet markdown; None when no usable text."""
    text = str(_pick(item, "text", "full_text", "content") or "").strip()
    if not text or text in {"[deleted]", "[removed]"}:
        return None

    handle = str(
        _pick(item, "userName", "screen_name", "screenName", "authorUsername") or ""
    ).strip().lstrip("@")
    name = str(_pick(item, "name", "authorName", "user.name") or "").strip()

    header = "# X/Tweet"
    if handle and name and name != handle:
        header += f" by @{handle} ({name})"
    elif handle:
        header += f" by @{handle}"

    lines = [header, ""]
    lines.extend(sanitize_markdown(text).split("\n"))
    lines.append("")

    stats: list[str] = []
    created = str(_pick(item, "createdAt", "created_at", "date") or "").strip()
    if created:
        stats.append(f"Date: {created}")
    favorites = _pick(item, "favoriteCount", "likeCount", "likes")
    retweets = _pick(item, "retweetCount", "shares")
    replies = _pick(item, "replyCount", "repliesCount", "comments")
    views = _pick(item, "viewCount", "views", "impressions")
    for label, value in (
        ("Likes", favorites),
        ("Retweets", retweets),
        ("Replies", replies),
        ("Views", views),
    ):
        if value not in (None, ""):
            stats.append(f"{label}: {value}")

    permalink = str(
        _pick(item, "url", "tweetUrl", "permalink", "link") or ""
    ).strip()
    if handle and not permalink:
        tweet_id = str(_pick(item, "id", "id_str", "tweet_id", "conversationId") or "")
        if tweet_id.isdigit():
            permalink = f"https://x.com/{handle}/status/{tweet_id}"
    if permalink:
        stats.append(f"Link: {permalink}")

    if stats:
        lines.append(" | ".join(stats))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


async def fetch_twitter_markdown(url: str) -> str:
    """Fetch an X/Twitter status or profile timeline as LLM-ready markdown."""
    target = parse_twitter_url(url)

    client = get_apify_client()
    if client is None:
        raise TwitterError("Apify layer unavailable: APIFY_API_TOKEN is not configured")

    from ...settings import settings

    if target.tweet_id:
        canonical = f"https://x.com/i/web/status/{target.tweet_id}"
        run_input: dict[str, Any] = {"urls": [canonical]}
    else:
        assert target.screen_name is not None
        run_input = {"profiles": [target.screen_name]}

    items = await client.run_sync_get_dataset_items(settings.apify_twitter_actor, run_input)

    rendered: list[str] = []
    seen_ids: set[str] = set()
    for item in items:
        item_id = str(_pick(item, "id", "id_str", "tweet_id") or "")
        if target.tweet_id and item_id and item_id != target.tweet_id:
            continue
        if item_id and item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        md = _render_tweet_item(item)
        if md:
            rendered.append(md)
        if len(rendered) >= 10 and not target.tweet_id:
            break

    if not rendered:
        raise TwitterError(f"Apify actor returned no renderable tweets for {url}")

    if target.screen_name:
        body = "\n---\n\n".join(rendered)
        return f"# X/Timeline @{target.screen_name}\n\n{body}"
    return rendered[0]
