"""Provider-neutral conversion from acquired document models to Markdown.

Acquisition adapters retain source payloads and declared formats. This module is
 the only boundary that turns those neutral payloads into Markdown before the
shared :class:`MarkdownProcessor` evaluates them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .html_tools import extract_links, html_to_markdown
from .models import (
    PackageDocument,
    RawDocument,
    RepositoryDocument,
    TextDocument,
    ThreadDocument,
    ThreadMessage,
)


class RenderedBody(BaseModel):
    """Rendered Markdown plus links discovered while converting the payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    markdown: str
    links: tuple[dict[str, Any], ...] = Field(default_factory=tuple)


_SAFE_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+.-]{1,32}$")


def _heading(value: str, fallback: str) -> str:
    """Return one safe single-line Markdown heading value."""
    cleaned = " ".join(str(value or "").split())
    return cleaned or fallback


def _literal_text(document: TextDocument) -> RenderedBody:
    """Preserve declared plain text as a literal fenced payload."""
    if not document.text:
        return RenderedBody(markdown="")
    language = (
        document.language
        if document.language and _SAFE_LANGUAGE_RE.fullmatch(document.language)
        else "text"
    )
    body = document.text.rstrip("\n")
    return RenderedBody(markdown=f"```{language}\n{body}\n```")


def render_text_document(document: TextDocument, *, source_url: str) -> RenderedBody:
    """Convert one text payload according to its declared syntax."""
    if document.format == "markdown":
        return RenderedBody(markdown=document.text)
    if document.format == "html":
        markdown = html_to_markdown(document.text, url=source_url)
        links = extract_links(document.text, base_url=source_url)
        return RenderedBody(markdown=markdown, links=tuple(links))
    return _literal_text(document)


def _render_message_body(message: ThreadMessage, *, source_url: str) -> RenderedBody:
    """Render a thread message without interpreting literal text as Markdown."""
    return render_text_document(
        TextDocument(message.body, format=message.body_format),
        source_url=message.permalink or source_url,
    )


def _message_metadata(message: ThreadMessage) -> list[str]:
    """Return stable source metadata for one thread message."""
    values: list[str] = [f"- Message ID: `{message.id}`", f"- Role: `{message.role}`"]
    if message.parent_id:
        values.append(f"- Parent ID: `{message.parent_id}`")
    if message.author:
        values.append(f"- Author: {message.author}")
    if message.created_at:
        values.append(f"- Created: {message.created_at}")
    if message.score is not None:
        values.append(f"- Score: {message.score}")
    if message.accepted:
        values.append("- Accepted answer: yes")
    if message.permalink:
        values.append(f"- Permalink: {message.permalink}")
    return values


def render_thread_document(document: ThreadDocument) -> RenderedBody:
    """Render a source-ordered thread while preserving message boundaries."""
    lines = [f"# {_heading(document.title, 'Thread')}", "", f"Source: {document.url}", ""]
    links: list[dict[str, Any]] = []
    if document.metadata:
        metadata = json.dumps(document.metadata, ensure_ascii=False, sort_keys=True, default=str)
        lines.extend(["## Thread metadata", "", f"```json\n{metadata}\n```", ""])
    for index, message in enumerate(document.messages, start=1):
        rendered = _render_message_body(message, source_url=document.url)
        lines.extend([f"## Message {index}: {_heading(message.role, 'Comment')}", ""])
        lines.extend(_message_metadata(message))
        lines.extend(["", rendered.markdown.rstrip(), ""])
        links.extend(rendered.links)
        if message.permalink:
            links.append({"url": message.permalink, "text": message.role, "internal": True})
    return RenderedBody(markdown="\n".join(lines).rstrip() + "\n", links=tuple(links))


def _render_links(links: tuple[dict[str, Any], ...]) -> list[str]:
    """Render structured source links without losing their original labels."""
    lines: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        label = str(link.get("label") or link.get("text") or "link").strip()
        href = str(link.get("href") or link.get("url") or "").strip()
        if href:
            lines.append(f"- [{label}]({href})")
    return lines


def _render_package_document(document: PackageDocument) -> RenderedBody:
    """Render registry metadata and the package README."""
    readme = render_text_document(document.readme, source_url=document.url)
    lines = [f"# {_heading(document.name, 'Package')}", "", f"Source: {document.url}"]
    if document.registry:
        lines.append(f"- Registry: `{document.registry}`")
    if document.version:
        lines.append(f"- Version: `{document.version}`")
    if document.summary:
        lines.extend(["", document.summary.strip()])
    if document.dependencies:
        lines.extend(
            ["", "## Dependencies", "", *[f"- `{item}`" for item in document.dependencies]]
        )
    link_lines = _render_links(document.links)
    if link_lines:
        lines.extend(["", "## Project links", "", *link_lines])
    if document.metadata:
        metadata = json.dumps(document.metadata, ensure_ascii=False, sort_keys=True, default=str)
        lines.extend(["", "## Package metadata", "", f"```json\n{metadata}\n```"])
    lines.extend(["", "## README", "", readme.markdown.rstrip()])
    return RenderedBody(
        markdown="\n".join(lines).rstrip() + "\n",
        links=(*readme.links, *document.links),
    )


def _render_repository_document(document: RepositoryDocument) -> RenderedBody:
    """Render repository metadata, file names, and README content."""
    readme = render_text_document(document.readme, source_url=document.url)
    lines = [f"# {_heading(document.name, 'Repository')}", "", f"Source: {document.url}"]
    link_lines = _render_links(document.links)
    if link_lines:
        lines.extend(["", "## Repository links", "", *link_lines])
    if document.files:
        lines.extend(["", "## Top-level files", "", *[f"- `{name}`" for name in document.files]])
    if document.metadata:
        metadata = json.dumps(document.metadata, ensure_ascii=False, sort_keys=True, default=str)
        lines.extend(["", "## Repository metadata", "", f"```json\n{metadata}\n```"])
    lines.extend(["", "## README", "", readme.markdown.rstrip()])
    return RenderedBody(
        markdown="\n".join(lines).rstrip() + "\n",
        links=(*readme.links, *document.links),
    )


def render_raw_document(document: RawDocument) -> RenderedBody:
    """Convert a neutral :class:`RawDocument` into Markdown exactly once."""
    body = document.body
    if isinstance(body, TextDocument):
        rendered = render_text_document(body, source_url=document.fetched_url or document.input_url)
        if document.title and body.format == "html":
            title = _heading(document.title, "Document")
            rendered = RenderedBody(
                markdown=f"# {title}\n\n{rendered.markdown.rstrip()}\n",
                links=rendered.links,
            )
    elif isinstance(body, ThreadDocument):
        rendered = render_thread_document(body)
    elif isinstance(body, PackageDocument):
        rendered = _render_package_document(body)
    elif isinstance(body, RepositoryDocument):
        rendered = _render_repository_document(body)
    else:  # pragma: no cover - RawDocument is a closed discriminated union
        raise TypeError(f"Unsupported RawDocument body: {type(body).__name__}")

    merged_links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for link in (*document.links, *rendered.links):
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or link.get("href") or "").strip()
        text = str(link.get("text") or link.get("label") or "").strip()
        key = (url, text)
        if url and key not in seen:
            seen.add(key)
            merged_links.append(dict(link))
    return RenderedBody(markdown=rendered.markdown, links=tuple(merged_links))


__all__ = [
    "RenderedBody",
    "render_raw_document",
    "render_text_document",
    "render_thread_document",
]
