"""Small, fail-open /llms.txt preflight for the unified fetch tool."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .safe_fetch import SafeFetchError, safe_fetch_url


@dataclass(frozen=True)
class LlmsTxtResult:
    available: bool
    url: str | None = None
    content: str | None = None
    content_type: str | None = None
    error: str | None = None


def _root_url(url: str) -> str | None:
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
    llms_url = _root_url(url)
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
