"""Reddit thread producer returning RawDocument candidates."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..documents import build_thread_document, thread_messages_from_dict
from ..http_utils import raise_for_status, request_with_redirect_validation
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    ThreadDocument,
)


class RedditError(RuntimeError):
    """Legacy Reddit parse error retained for non-registry callers."""


LOGGER = logging.getLogger(__name__)


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


def match_reddit(parsed: ParsedURL) -> ResolverTarget | None:
    try:
        target = parse_reddit_url(parsed.url)
    except RedditError:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="reddit",
        values={"subreddit": target.subreddit, "post_id": target.post_id},
    )


async def fetch_reddit_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    subreddit = target.values.get("subreddit")
    post_id = target.values.get("post_id")
    if not subreddit or not post_id:
        raise AcquisitionError(
            code="bad_target", message="Reddit target missing subreddit or post_id"
        )
    if subreddit == "auto":
        api_url = f"https://www.reddit.com/comments/{post_id}.json?limit=50&sort=confidence"
    else:
        api_url = (
            f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json?limit=50&sort=confidence"
        )
    headers = {
        "User-Agent": "kindly-web-search/1.0 (reddit resolver)",
        "Accept": "application/json, text/plain, */*",
    }
    response = await request_with_redirect_validation(
        ctx, api_url, headers=headers, follow_redirects=True
    )
    raise_for_status(response, what="Reddit JSON")
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(
            code="json_parse_error", message=str(exc), status="error", retryable=False
        ) from exc
    if not isinstance(data, list) or len(data) < 2:
        raise AcquisitionError(code="empty_response", message="Reddit returned no listing data")
    post_listing = data[0].get("data", {}).get("children", []) if isinstance(data[0], dict) else []
    if not post_listing or not isinstance(post_listing[0], dict):
        raise AcquisitionError(code="empty_response", message="Reddit post data missing")
    post_data = post_listing[0].get("data") or {}
    comments_data = data[1].get("data", {}).get("children", []) if isinstance(data[1], dict) else []
    messages = thread_messages_from_dict(post=post_data, comments=comments_data)
    title = str(post_data.get("title") or f"Reddit {post_id}")
    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=messages,
        metadata={
            "subreddit": subreddit,
            "post_id": post_id,
            "score": post_data.get("score"),
            "permalink": str(post_data.get("permalink") or "") or None,
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="reddit_fetched",
            message=f"Reddit {subreddit}/{post_id} ({len(messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="reddit",
        fetch_backend="reddit_json",
        body=thread,
        title=title,
        metadata={
            "subreddit": subreddit,
            "post_id": post_id,
            "comment_count": len([m for m in messages if m.role != "post"]),
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )
