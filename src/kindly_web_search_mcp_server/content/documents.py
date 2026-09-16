"""Builders for the neutral document contracts in :mod:`.models`.

Two concerns share this module because several resolver adapters depend on
them identically:

* **Thread reducers** — :func:`thread_messages_from_dict`,
  :func:`thread_messages_flat`, and :func:`build_thread_document` turn
  platform-specific API envelopes into :class:`ThreadDocument` payloads.
* **Repository builder** — :func:`build_repository_document` turns a README
  plus file/link metadata into a :class:`RepositoryDocument` (GitHub and
  Hugging Face resolvers).

Rendering happens in :mod:`.renderers`; evaluation in
:mod:`.markdown_processor`. Per-registry package payload fetchers and
builders live inside their owning resolver modules, not here."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import (
    PackageDocument,
    RepositoryDocument,
    TextDocument,
    TextFormat,
    ThreadDocument,
    ThreadMessage,
)

# ---------------------------------------------------------------------------
# Thread builders (Reddit-shaped reducer shared by every forum adapter)
# ---------------------------------------------------------------------------


_REDDIT_PLACEHOLDER_BODIES = frozenset({"[deleted]", "[removed]"})


def _coerce_format(value: Any, default: TextFormat = "markdown") -> TextFormat:
    """Return a valid :class:`TextFormat` for any source value."""

    if isinstance(value, str) and value.lower() in {"markdown", "html", "text"}:
        return value.lower()  # type: ignore[return-value]
    return default


def _format_reddit_body(body: Any) -> str:
    """Lift a Reddit selftext/comment body into a plain string."""

    if body is None:
        return ""
    text = str(body).strip()
    if text in _REDDIT_PLACEHOLDER_BODIES:
        return ""
    return text


def thread_messages_from_dict(
    *,
    post: dict[str, Any] | None = None,
    comments: Iterable[dict[str, Any]] | None = None,
    fallback_role: str = "comment",
    body_format: TextFormat = "markdown",
) -> tuple[ThreadMessage, ...]:
    """Build a thread root + reply chain from a Reddit-shaped payload.

    Reddit is the most expressive schema: other producers reduce to it via a
    thin adapter before calling this helper.
    """

    messages: list[ThreadMessage] = []
    if post:
        body = _format_reddit_body(post.get("body") or post.get("selftext"))
        messages.append(
            ThreadMessage(
                id=str(post.get("id") or "root"),
                role="post",
                body=body,
                body_format=body_format,
                author=str(post.get("author") or "anonymous").strip(),
                created_at=str(post.get("created_at") or "") or None,
                score=post.get("score") if isinstance(post.get("score"), int) else None,
                accepted=bool(post.get("accepted")),
                permalink=str(post.get("permalink") or "") or None,
                parent_id=None,
            )
        )

    def _walk(items: Iterable[dict[str, Any]] | None, parent_id: str | None) -> None:
        if not items:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in (None, "t1", "comment"):
                continue
            raw_data = item.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else item
            message_id = str(data.get("id") or "")
            if not message_id:
                continue
            body = _format_reddit_body(data.get("body") or data.get("selftext") or data.get("text"))
            messages.append(
                ThreadMessage(
                    id=message_id,
                    role=str(data.get("role") or fallback_role),
                    body=body,
                    body_format=_coerce_format(data.get("body_format"), body_format),
                    author=str(data.get("author") or "anonymous").strip(),
                    created_at=str(data.get("created_at") or "") or None,
                    score=data.get("score") if isinstance(data.get("score"), int) else None,
                    accepted=bool(data.get("is_accepted") or data.get("accepted")),
                    permalink=str(data.get("permalink") or "") or None,
                    parent_id=parent_id,
                )
            )
            replies = data.get("replies")
            if isinstance(replies, dict):
                replies_data = replies.get("data")
                children: Any = None
                if isinstance(replies_data, dict):
                    children = replies_data.get("children")
                if isinstance(children, list):
                    _walk(children, message_id)
            elif isinstance(replies, list):
                _walk(replies, message_id)

    _walk(comments, parent_id=str(post.get("id") or "root") if post else None)
    return tuple(messages)


def thread_messages_flat(
    items: Iterable[dict[str, Any]],
    *,
    fallback_role: str = "comment",
    body_format: TextFormat = "markdown",
) -> tuple[ThreadMessage, ...]:
    """Flatten an already-source-ordered list of message dicts into :class:`ThreadMessage`."""

    out: list[ThreadMessage] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id") or "")
        if not message_id:
            continue
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        out.append(
            ThreadMessage(
                id=message_id,
                role=str(item.get("role") or fallback_role),
                body=body,
                body_format=_coerce_format(item.get("body_format"), body_format),
                author=str(item.get("author") or "anonymous").strip(),
                created_at=str(item.get("created_at") or "") or None,
                score=item.get("score") if isinstance(item.get("score"), int) else None,
                accepted=bool(item.get("accepted") or item.get("is_accepted")),
                permalink=str(item.get("permalink") or "") or None,
                parent_id=str(item.get("parent_id") or "") or None,
            )
        )
    return tuple(out)


def build_thread_document(
    *,
    title: str,
    url: str,
    messages: tuple[ThreadMessage, ...],
    metadata: dict[str, Any] | None = None,
) -> ThreadDocument:
    """Assemble a :class:`ThreadDocument` and validate one message carries id+body."""

    if not messages:
        raise ValueError("ThreadDocument requires at least one message")
    if not any(message.body for message in messages):
        raise ValueError("ThreadDocument requires at least one non-empty body")
    coverage: dict[str, Any] = {
        "message_count": len(messages),
        "root_id": messages[0].id,
    }
    merged_metadata = dict(metadata or {})
    merged_metadata.setdefault("coverage", coverage)
    return ThreadDocument(
        title=title.strip() or "Thread",
        url=url,
        messages=messages,
        metadata=merged_metadata,
    )


__all__ = [
    "build_package_document",
    "build_repository_document",
    "build_thread_document",
    "thread_messages_flat",
    "thread_messages_from_dict",
]


# ---------------------------------------------------------------------------
# Package / repository builders and per-registry fetch helpers
# ---------------------------------------------------------------------------
def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow an untyped payload to a dict for pyright-safe access."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Narrow an untyped payload to a list for pyright-safe access."""
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str | None:
    """Narrow an untyped payload to a string."""
    return value if isinstance(value, str) else None


_README_LITERAL_FORMATS = frozenset({"text/markdown", "text/x-markdown", "text/plain"})


def build_package_document(
    *,
    name: str,
    url: str,
    registry: str,
    summary: str = "",
    version: str | None = None,
    readme_text: str = "",
    readme_format: str = "text/markdown",
    dependencies: tuple[str, ...] = (),
    links: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
) -> PackageDocument:
    """Wrap a registry payload into a :class:`PackageDocument`.

    README format is preserved literally even when it is not natively renderable
    (e.g. ``application/octet-stream`` for Crates.io tarballs); the renderer is
    expected to make a fallback decision later.
    """

    body = TextDocument(
        text=readme_text, format="markdown" if readme_format in _README_LITERAL_FORMATS else "text"
    )
    return PackageDocument(
        name=name,
        url=url,
        registry=registry,
        summary=summary,
        version=version,
        readme=body,
        dependencies=dependencies,
        links=links,
        metadata=dict(metadata or {}),
    )


def build_repository_document(
    *,
    name: str,
    url: str,
    readme_text: str = "",
    readme_format: str = "text/markdown",
    files: tuple[str, ...] = (),
    links: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
) -> RepositoryDocument:
    """Wrap a repository payload into a :class:`RepositoryDocument`."""

    body = TextDocument(
        text=readme_text, format="markdown" if readme_format in _README_LITERAL_FORMATS else "text"
    )
    return RepositoryDocument(
        name=name,
        url=url,
        readme=body,
        files=files,
        links=links,
        metadata=dict(metadata or {}),
    )


def _link_from_pairs(pairs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    cleaned: list[dict[str, Any]] = []
    for label, target in pairs.items():
        if not target:
            continue
        cleaned.append({"label": str(label), "href": str(target)})
    return tuple(cleaned)


# ---------------------------------------------------------------------------
# Per-registry fetch helpers (transport + metadata only).
# ---------------------------------------------------------------------------


__all__ = [
    "build_repository_document",
]
