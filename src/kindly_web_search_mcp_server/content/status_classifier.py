from __future__ import annotations

from dataclasses import dataclass

from .artifact import ContentStatus


@dataclass(frozen=True)
class ClassificationResult:
    status: ContentStatus
    reason: str | None
    cacheable: bool


_BLOCK_PATTERNS = (
    "access denied",
    "forbidden",
    "captcha",
    "verify you are human",
    "cloudflare",
    "please enable javascript",
)

_ERROR_PATTERNS = (
    "err_unsafe_port",
    "err_connection_refused",
    "err_connection_timed_out",
    "err_name_not_resolved",
    "this site can’t be reached",
    "this site can't be reached",
    "chrome-error://chromewebdata",
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def classify_markdown(markdown: str) -> ClassificationResult:
    normalized = _normalize(markdown)
    if not normalized:
        return ClassificationResult(status="error", reason="empty_content", cacheable=False)

    for pattern in _ERROR_PATTERNS:
        if pattern in normalized:
            return ClassificationResult(status="error", reason="browser_error_page", cacheable=False)

    for pattern in _BLOCK_PATTERNS:
        if pattern in normalized:
            return ClassificationResult(status="blocked", reason="access_blocked", cacheable=False)

    words = len(normalized.split())
    if words < 30:
        return ClassificationResult(status="partial", reason="too_short", cacheable=False)

    return ClassificationResult(status="success", reason=None, cacheable=True)
