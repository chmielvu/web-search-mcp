"""llms.txt resolver: match root and explicit ``/llms.txt`` URLs, probe, fetch.

``match_llms_txt`` claims site roots and explicit ``/llms.txt`` paths; the
fetch probes the candidate URL and yields a candidate only for non-empty,
text-like responses. The probe historically accepted root URLs only, which
made the explicit-path registry branch unreachable; both cases work now.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..http_utils import SafeFetchError, safe_fetch_url
from ..models import (
    AcquisitionError,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    TextDocument,
)


@dataclass(frozen=True)
class LlmsTxtResult:
    """Outcome of a root ``/llms.txt`` availability probe."""

    available: bool
    url: str | None = None
    content: str | None = None
    content_type: str | None = None
    error: str | None = None


def _llms_candidate_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.path not in {"", "/"}:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/llms.txt"


async def check_llms_txt(
    url: str,
    *,
    timeout_seconds: float = 5.0,
    max_response_bytes: int = 5 * 1024 * 1024,
) -> LlmsTxtResult:
    """Check a root URL for a non-empty, text-like /llms.txt document."""
    llms_url = _llms_candidate_url(url)
    if llms_url is None:
        return LlmsTxtResult(available=False)
    try:
        fetched = await safe_fetch_url(
            llms_url,
            timeout_seconds=min(max(timeout_seconds, 1.0), 5.0),
            max_response_bytes=max_response_bytes,
        )
    except SafeFetchError as exc:
        return LlmsTxtResult(available=False, url=llms_url, error=exc.code)
    except Exception as exc:  # pragma: no cover - defensive fail-open boundary
        return LlmsTxtResult(available=False, url=llms_url, error=type(exc).__name__)

    content = fetched.text or fetched.body.decode("utf-8", errors="replace")
    content_type = fetched.content_type or ""
    media_type = content_type.split(";", 1)[0].strip().lower()
    if not content.strip() or media_type not in {"", "text/plain", "text/markdown"}:
        return LlmsTxtResult(
            available=False,
            url=llms_url,
            content_type=content_type,
            error="unsupported_content_type" if media_type else "empty_content",
        )
    return LlmsTxtResult(
        available=True,
        url=fetched.fetched_url or llms_url,
        content=content,
        content_type=content_type or "text/plain",
    )


def match_llms_txt(parsed: ParsedURL) -> ResolverTarget | None:
    host = (parsed.parts.hostname or "").lower()
    if not host:
        return None
    path = parsed.parts.path or ""
    if path in {"/llms.txt", "/llms-full.txt", "", "/"}:
        return ResolverTarget(url=parsed.url, kind="llms_txt", values={"url": parsed.url})
    return None


async def fetch_llms_txt_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a root llms.txt candidate document."""
    url = target.values.get("url") or target.url
    probe = await check_llms_txt(url, max_response_bytes=ctx.max_response_bytes)
    if not probe.available or not (probe.content or "").strip():
        raise AcquisitionError(code="llms_txt_unavailable", message="No llms.txt document found.")
    return RawDocument(
        input_url=url,
        fetched_url=probe.url or url,
        source_type="llms_txt",
        fetch_backend="llms_txt",
        body=TextDocument(text=probe.content or "", format="markdown"),
        content_type=probe.content_type or "text/plain",
        title=None,
        metadata={"source": "llms.txt", "url": probe.url},
        links=(),
        diagnostics=(),
        http_status=None,
        response_headers={},
        complete=True,
        scope="full",
        bytes_downloaded=None,
        redirect_count=None,
    )
