"""Specialized resolver for Discourse forum topics (https://<forum>/t/<slug>/<id>).

Fetches structured topic metadata and posts directly from the Discourse JSON API.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ..documents import build_thread_document
from ..html_tools import html_to_markdown as extract_html_as_markdown
from ..http_utils import request_with_redirect_validation
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    ThreadDocument,
    ThreadMessage,
)


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


# ---------------------------------------------------------------------------
# Raw producer.
# ---------------------------------------------------------------------------


def match_discourse(parsed: ParsedURL) -> ResolverTarget | None:
    target = parse_discourse_url(parsed.url)
    if target is None:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="discourse",
        values={
            "base_url": target.base_url,
            "topic_id": target.topic_id,
            "slug": target.slug or "",
        },
    )


async def fetch_discourse_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    base_url = target.values.get("base_url")
    topic_id = target.values.get("topic_id")
    if not base_url or not topic_id:
        raise AcquisitionError(
            code="bad_target", message="Discourse target missing base_url or topic_id"
        )
    api_url = f"{base_url}/t/{topic_id}.json"
    response = await request_with_redirect_validation(ctx, api_url, follow_redirects=True)
    if response.status_code == 404:
        raise AcquisitionError(
            code="not_found", message=f"Discourse topic {topic_id} not found", retryable=False
        )
    if response.status_code >= 400:
        raise AcquisitionError(
            code=f"http_{response.status_code}",
            message=f"Discourse returned HTTP {response.status_code}",
            status="error",
            retryable=False,
        )
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(
            code="json_parse_error", message=str(exc), status="error", retryable=False
        ) from exc
    if not isinstance(data, dict):
        raise AcquisitionError(
            code="bad_payload",
            message="Discourse payload not an object",
            status="error",
            retryable=False,
        )
    title = str(data.get("title") or f"Discourse {topic_id}").strip()
    posts = (
        data.get("post_stream", {}).get("posts")
        if isinstance(data.get("post_stream"), dict)
        else None
    )
    if not isinstance(posts, list) or not posts:
        raise AcquisitionError(
            code="no_posts", message="Discourse topic has no posts", status="error", retryable=False
        )

    messages: list[ThreadMessage] = []
    post_by_index: dict[int, dict[str, Any]] = {}
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_index = post.get("post_number") if isinstance(post.get("post_number"), int) else None
        if post_index is not None:
            post_by_index[post_index] = post

    for post in posts:
        if not isinstance(post, dict):
            continue
        pid = str(post.get("id") or "")
        if not pid:
            continue
        cooked = str(post.get("cooked") or "")
        text_body = extract_html_as_markdown(cooked, url=target.url) if cooked else ""
        reply_to = (
            post.get("reply_to_post_number")
            if isinstance(post.get("reply_to_post_number"), int)
            else None
        )
        parent_pid: str | None = None
        if reply_to is not None and reply_to in post_by_index:
            parent_pid = str(post_by_index[reply_to].get("id") or "") or None
        role = "question" if post.get("post_number") == 1 else "comment"
        username = None
        if isinstance(post.get("username"), str):
            username = post["username"]
        messages.append(
            ThreadMessage(
                id=pid,
                role=role,
                body=text_body,
                body_format="markdown",
                author=username,
                created_at=str(post.get("created_at") or "") or None,
                parent_id=parent_pid,
                permalink=str(post.get("post_url") or "") or None,
            )
        )

    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=tuple(messages),
        metadata={
            "base_url": base_url,
            "topic_id": topic_id,
            "views": data.get("views"),
            "like_count": data.get("like_count"),
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="discourse_fetched",
            message=f"Discourse {base_url} topic {topic_id} ({len(messages)} posts)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="discourse_topic",
        fetch_backend="discourse_json",
        body=thread,
        title=title,
        metadata={
            "base_url": base_url,
            "topic_id": topic_id,
            "views": data.get("views"),
            "post_count": len(messages),
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )
