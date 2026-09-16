"""HackerNews resolver — thread producer returning RawDocument candidates."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..documents import build_thread_document, thread_messages_flat
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


class HackerNewsError(RuntimeError):
    pass


@dataclass(frozen=True)
class HackerNewsTarget:
    item_id: int


_HN_HOSTS = frozenset({"news.ycombinator.com", "www.news.ycombinator.com", "hn.algolia.com"})


def parse_hackernews_url(url: str) -> HackerNewsTarget:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _HN_HOSTS:
        raise HackerNewsError(f"Unsupported HackerNews host: {host or '(missing)'}")
    item_id: int | None = None
    if "algolia.com" in host:
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[-1].isdigit():
            item_id = int(parts[-1])
    else:
        qs = parse_qs(parsed.query)
        id_list = qs.get("id") or []
        if id_list and id_list[0].isdigit():
            item_id = int(id_list[0])
    if item_id is None:
        raise HackerNewsError("URL does not contain a valid HackerNews item ID.")
    return HackerNewsTarget(item_id=item_id)


def match_hackernews(parsed: ParsedURL) -> ResolverTarget | None:
    try:
        target = parse_hackernews_url(parsed.url)
    except HackerNewsError:
        return None
    return ResolverTarget(
        url=parsed.url, kind="hackernews", values={"item_id": str(target.item_id)}
    )


# ----- Legacy markdown renderer kept verbatim below -----


def _hn_html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<p>", "\n\n", raw_html)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


# ----- Raw producer entry point -----


async def fetch_hackernews_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    try:
        item_id = int(target.values.get("item_id", ""))
    except Exception as exc:
        raise AcquisitionError(code="bad_target", message=str(exc)) from exc
    api_url = f"https://hn.algolia.com/api/v1/items/{item_id}"
    response = await request_with_redirect_validation(ctx, api_url, follow_redirects=True)
    raise_for_status(response, what="HackerNews Algolia")
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(
            code="json_parse_error", message=str(exc), status="error", retryable=False
        ) from exc
    if not isinstance(data, dict):
        raise AcquisitionError(code="bad_payload", message="HackerNews response missing")

    flat_messages: list[dict[str, Any]] = []
    root_id = str(data.get("id") or f"hn_{item_id}")

    def _walk(node: dict[str, Any], parent_id: str | None) -> None:
        author = str(node.get("author") or node.get("by") or "anonymous")
        text = _hn_html_to_text(str(node.get("text") or node.get("story_text") or ""))
        if text:
            flat_messages.append(
                {
                    "id": str(node.get("id") or f"hn_{len(flat_messages)}"),
                    "role": "post" if not flat_messages else "comment",
                    "body": text,
                    "body_format": "markdown",
                    "author": author,
                    "created_at": str(node.get("created_at") or "") or None,
                    "parent_id": parent_id,
                }
            )
        for child in node.get("children") or []:
            if isinstance(child, dict):
                _walk(child, str(node.get("id") or parent_id))

    if data.get("text") or data.get("story_text"):
        _walk(
            {
                "id": root_id,
                "author": data.get("author"),
                "text": data.get("text") or data.get("story_text"),
                "created_at": data.get("created_at"),
                "children": data.get("children") or [],
            },
            None,
        )
    else:
        # top-level story only — children are nested via kids in legacy form
        raw_kids: Any = data.get("kids")
        kids: list[Any] = raw_kids if isinstance(raw_kids, list) else []
        flat_messages.append(
            {
                "id": root_id,
                "role": "post",
                "body": "",
                "body_format": "markdown",
                "author": str(data.get("author") or data.get("by") or "anonymous"),
                "created_at": str(data.get("created_at") or "") or None,
                "parent_id": None,
            }
        )
        # Render kids fetched live
        for kid_id in kids:
            if not isinstance(kid_id, (int, str)):
                continue
            kid_url = f"https://hn.algolia.com/api/v1/items/{kid_id}"
            kid_response = await request_with_redirect_validation(
                ctx, kid_url, follow_redirects=True
            )
            if kid_response.status_code != 200:
                continue
            try:
                kid = kid_response.json()
            except Exception:
                continue
            if isinstance(kid, dict):
                _walk(kid, root_id)

    thread_messages = thread_messages_flat(flat_messages)
    title = str(data.get("title") or data.get("story_title") or f"HackerNews {item_id}").strip()
    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=thread_messages,
        metadata={
            "item_id": item_id,
            "score": data.get("points") or data.get("score"),
            "descendants": data.get("descendants") or data.get("children_count"),
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="hackernews_fetched",
            message=f"HackerNews item {item_id} ({len(thread_messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="hackernews",
        fetch_backend="hn_algolia",
        body=thread,
        title=title,
        metadata={
            "item_id": item_id,
            "score": data.get("points") or data.get("score"),
            "descendants": data.get("descendants") or data.get("children_count"),
        },
        diagnostics=diagnostics,
        complete=bool(thread_messages),
        scope="full",
    )


__all__ = [
    "HackerNewsError",
    "HackerNewsTarget",
    "fetch_hackernews_raw",
    "match_hackernews",
    "parse_hackernews_url",
]
