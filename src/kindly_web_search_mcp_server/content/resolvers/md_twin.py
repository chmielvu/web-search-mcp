"""Markdown-twin resolver: probe a page's ``.md`` sibling before the cascade.

Many documentation sites (pydantic.dev, Mintlify-hosted docs, ...) publish a
clean Markdown twin of every HTML page at the same path with a ``.md``
suffix. This resolver probes that twin with one bounded GET:

* Success → a ``RawDocument`` carrying already-clean Markdown. The shared
  processor accepts it, the registry loop breaks, Jina/Crawl4AI/browser are
  all skipped, and finalization runs on the twin verbatim.
* Miss (404, HTML response, empty body) → ``AcquisitionError``; the
  orchestrator records one attempt and the generic cascade continues.

The match is pure URL shaping; no network happens before ``fetch``.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from ..http_utils import (
    bytes_with_cap,
    raise_for_status,
    request_with_redirect_validation,
)
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    TextDocument,
)

# Media types eligible as a Markdown twin. Empty means the server omitted
# Content-Type; the HTML sniff below still guards that case.
_ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset(
    {"", "text/markdown", "text/x-markdown", "text/plain"}
)

_TWIN_HEADERS: dict[str, str] = {
    "User-Agent": "kindly-web-search/1.0",
    "Accept": "text/markdown, text/plain;q=0.9, text/html;q=0.1",
}


def md_twin_url(url: str) -> str | None:
    """Return the page's ``.md`` twin URL, or ``None`` when a twin makes no sense.

    Bare hosts (``https://example.com``), paths without a final segment, and
    extension-bearing targets (``file.md``, ``file.pdf``, ``data.json``) are
    rejected: the former has no page to twin, the latter belong to the
    deterministic document/raw-text resolvers ahead of this spec.
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path or ""
    if not path or path == "/":
        return None
    trimmed = path.rstrip("/")
    if not trimmed:
        return None
    last_segment = trimmed.rsplit("/", 1)[-1]
    if "." in last_segment:
        return None
    return urlunsplit((parts.scheme, parts.netloc, f"{trimmed}.md", parts.query, ""))


def match_md_twin(parsed: ParsedURL) -> ResolverTarget | None:
    """Claim any page-shaped URL by offering its ``.md`` twin."""
    md_url = md_twin_url(parsed.url)
    if md_url is None:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="md_twin",
        values={"md_url": md_url, "source_url": parsed.url},
    )


def _sniffs_as_html(text: str) -> bool:
    """Cheap guard against SPA catch-alls served without a useful content type."""
    head = text[:512].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


async def fetch_md_twin_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Fetch the ``.md`` twin and return a Markdown RawDocument, or raise."""
    source_url = target.values.get("source_url") or target.url
    md_url = target.values.get("md_url") or source_url
    try:
        response = await request_with_redirect_validation(
            ctx,
            md_url,
            headers=_TWIN_HEADERS,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise AcquisitionError(
            code="md_twin_transport",
            message=f"Markdown twin transport failed: {type(exc).__name__}",
            retryable=True,
        ) from exc
    raise_for_status(response, what="Markdown twin")

    body = bytes_with_cap(response, cap=ctx.max_response_bytes, what="Markdown twin")
    text = body.decode(response.encoding or "utf-8", errors="replace")

    media_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if (
        media_type == "text/html"
        or (media_type not in _ALLOWED_MEDIA_TYPES)
        or _sniffs_as_html(text)
    ):
        raise AcquisitionError(
            code="md_twin_not_markdown",
            message=f"Markdown twin served {media_type or 'untyped'} content.",
            status="error",
            retryable=False,
        )
    if not text.strip():
        raise AcquisitionError(
            code="empty_content",
            message="Markdown twin was empty.",
            retryable=False,
        )

    fetched_url = str(response.url) if str(response.url) != md_url else md_url
    return RawDocument(
        input_url=source_url,
        fetched_url=fetched_url,
        source_type="md_twin",
        fetch_backend="md_twin",
        body=TextDocument(text=text, format="markdown"),
        content_type=response.headers.get("content-type"),
        title=None,
        metadata={"md_twin": True, "requested_url": md_url},
        links=(),
        diagnostics=(
            Diagnostic(
                code="md_twin_used",
                message=f"Content served from the Markdown twin {fetched_url}",
                source="md_twin",
                phase="acquire",
            ),
        ),
        http_status=response.status_code,
        response_headers=dict(response.headers),
        complete=True,
        scope="full",
        bytes_downloaded=len(body),
        redirect_count=None,
    )


__all__ = ["md_twin_url", "match_md_twin", "fetch_md_twin_raw"]
