"""Specialized resolver for raw Markdown and plain text files (.md, .txt, .markdown, .text).

Avoids heavy extraction (Jina, Crawl4AI, BeautifulSoup, Camoufox, Firecrawl)
by performing a direct safe HTTP fetch and returning LLM-ready markdown or text.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..http_utils import SafeFetchError, safe_fetch_url
from ..machine_readable import detect_content_format, render_typed_content
from ..models import (
    AcquisitionError,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    TextDocument,
)

LOGGER = logging.getLogger(__name__)

RAW_TEXT_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".mdown",
    ".mkdn",
    ".txt",
    ".text",
    ".rst",
    ".org",
    ".log",
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".rss",
    ".atom",
    ".toml",
    ".rtf",
    ".vtt",
    ".srt",
    ".svg",
    ".xml",
    ".py",
    ".ts",
    ".js",
    ".rs",
    ".go",
    ".c",
    ".cpp",
    ".h",
    ".java",
    ".sh",
}
RAW_TEXT_HOSTS: set[str] = {"raw.githubusercontent.com", "gist.githubusercontent.com"}


async def fetch_raw_text_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Fetch a matched raw-text URL into a RawDocument candidate."""
    url = target.values.get("url") or target.url
    try:
        fetched = await safe_fetch_url(
            url,
            timeout_seconds=ctx.timeout(20.0),
            max_response_bytes=ctx.max_response_bytes,
        )
    except SafeFetchError as exc:
        raise AcquisitionError(code=exc.code, message=str(exc)) from exc
    text = fetched.text or fetched.body.decode("utf-8", errors="replace")
    if not text.strip():
        raise AcquisitionError(code="empty_content", message="Raw text response was empty.")
    typed_format = detect_content_format(url, fetched.content_type, text)
    if typed_format is not None:
        rendered, meta, typed_links = render_typed_content(typed_format, text, url)
        return RawDocument(
            input_url=url,
            fetched_url=fetched.fetched_url or url,
            source_type=typed_format,
            fetch_backend="typed_content",
            body=TextDocument(text=rendered, format="markdown"),
            content_type=fetched.content_type,
            title=None,
            metadata=dict(meta),
            links=tuple(typed_links),
            diagnostics=(),
            http_status=fetched.status_code,
            response_headers=dict(fetched.response_headers or {}),
            complete=True,
            scope="full",
            bytes_downloaded=len(fetched.body),
            redirect_count=None,
        )
    lowered_path = urllib.parse.urlparse(url).path.lower()
    is_markdown = lowered_path.endswith((".md", ".markdown", ".mdown", ".mkdn"))
    lowered_type = (fetched.content_type or "").split(";", 1)[0].strip().lower()
    if not is_markdown and lowered_type not in {"", "text/markdown", "text/x-markdown"}:
        body = _unsupported_format_text(
            lowered_type or "text/plain", url, text, fetched.content_type
        )
    else:
        body = TextDocument(text=text, format="markdown" if is_markdown else "text")
    return RawDocument(
        input_url=url,
        fetched_url=fetched.fetched_url or url,
        source_type="raw_text",
        fetch_backend="raw_text_fetch",
        body=body,
        content_type=fetched.content_type,
        title=None,
        metadata={},
        links=(),
        diagnostics=(),
        http_status=fetched.status_code,
        response_headers=dict(fetched.response_headers or {}),
        complete=True,
        scope="full",
        bytes_downloaded=len(fetched.body),
        redirect_count=None,
    )


def _unsupported_format_text(
    declared: str, source_url: str, text: str, content_type: str | None
) -> TextDocument:
    """Preserve an unsupported declared syntax as labeled literal content."""
    header = f"Unsupported declared format `{declared}` from {source_url}"
    if content_type:
        header += f" ({content_type})"
    return TextDocument(text=f"{header}\n\n```text\n{text.strip()}\n```", format="text")


def match_raw_text(parsed: ParsedURL) -> ResolverTarget | None:
    """Claim raw-text URLs, yielding to resolvers that own specific shapes.

    Excluded: ``/blob/`` views (github/gitlab serve rendered HTML there, not
    the file) and ``llms.txt``/``llms-full.txt`` documents, which the
    ``llms_txt`` resolver owns.
    """
    host = (parsed.parts.hostname or "").lower()
    path = (parsed.parts.path or "").lower()
    if host in RAW_TEXT_HOSTS:
        return ResolverTarget(url=parsed.url, kind="raw_text", values={"url": parsed.url})
    if path.endswith(tuple(RAW_TEXT_EXTENSIONS)):
        if "/blob/" in path or path.rsplit("/", 1)[-1] in {"llms.txt", "llms-full.txt"}:
            return None
        return ResolverTarget(url=parsed.url, kind="raw_text", values={"url": parsed.url})
    if ("github.com" in host or "gitlab.com" in host) and "/raw/" in path:
        return ResolverTarget(url=parsed.url, kind="raw_text", values={"url": parsed.url})
    return None
