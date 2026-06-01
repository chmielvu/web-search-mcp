"""Content status classification with extended quality detection.

Detects junk/blocked/error pages before they reach the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .artifact import ContentStatus


@dataclass(frozen=True)
class ClassificationResult:
    status: ContentStatus
    reason: str | None
    cacheable: bool


# ------------------------------------------------------------------
# Extended pattern sets
# ------------------------------------------------------------------

_BLOCK_PATTERNS: tuple[str, ...] = (
    # Cloudflare / anti-bot
    "access denied",
    "verify you are human",
    "checking your browser",
    "please enable javascript",
    "please turn javascript on",
    "cloudflare",
    # Generic access blocks
    "forbidden",
    "captcha",
    "your request has been blocked",
    "you have been blocked",
)

_LOGIN_WALL_PATTERNS: tuple[str, ...] = (
    "sign in to continue",
    "sign in to view",
    "log in to continue",
    "log in to view",
    "please login",
    "please log in",
    "create an account to",
    "sign up to continue",
    "you need to be logged in",
    "login required",
    "authentication required",
    "this content is for registered users",
)

_PAYWALL_PATTERNS: tuple[str, ...] = (
    "subscribe to read",
    "subscribe to continue",
    "premium content",
    "premium article",
    "upgrade to access",
    "upgrade your plan",
    "this is a subscriber-only",
    "you've reached your free article limit",
    "you have reached your limit",
    "members only",
    "become a member",
)

_ERROR_PATTERNS: tuple[str, ...] = (
    "err_unsafe_port",
    "err_connection_refused",
    "err_connection_timed_out",
    "err_name_not_resolved",
    "this site can\u2019t be reached",
    "this site can't be reached",
    "chrome-error://chromewebdata",
    # HTTP status in page body
    "404 not found",
    "page not found",
    "500 internal server error",
    "503 service unavailable",
    "403 forbidden",
    "502 bad gateway",
    "the requested url was not found",
    "this page doesn't exist",
    "this page does not exist",
)

_COOKIE_CONSENT_INDICATORS: tuple[str, ...] = (
    "cookie",
    "privacy policy",
    "gdpr",
    "data protection",
    "we use cookies",
    "this site uses cookies",
    "accept all cookies",
)

_REDIRECT_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:https?://)?[^\s]+\s*$"),  # Single URL line
    re.compile(r"^redirect(?:ing)?\s+to\s+https?://", re.IGNORECASE),
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _pattern_match(normalized: str, patterns: tuple[str, ...]) -> str | None:
    """Return the first matching pattern or None."""
    for pattern in patterns:
        if pattern in normalized:
            return pattern
    return None


def _non_printable_ratio(text: str) -> float:
    """Ratio of non-printable characters in text.

    Control chars (0x00-0x1F except tab/newline), null bytes,
    Unicode replacement char, and other suspicious bytes.
    """
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        cp = ord(ch)
        if cp == 0xFFFD:  # Unicode replacement character
            bad += 1
        elif cp == 0x00:  # Null byte
            bad += 1
        elif cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):  # Controls except tab/lf/cr
            bad += 1
    return bad / len(text)


def _cookie_boilerplate_ratio(normalized: str) -> float:
    """Estimate how much of the page is cookie/GDPR boilerplate.

    Heuristic: count words matching cookie-consent indicators
    and divide by total words.
    """
    words = normalized.split()
    if not words:
        return 0.0
    cookie_count = sum(
        1
        for w in words
        if any(indicator in w for indicator in _COOKIE_CONSENT_INDICATORS)
    )
    return cookie_count / len(words)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def classify_markdown(markdown: str) -> ClassificationResult:
    """Classify extracted page content for quality/block/error signals.

    Returns a ClassificationResult with status and reason.
    This is used by the content resolution pipeline to decide
    whether to return or discard content.
    """
    normalized = _normalize(markdown)
    if not normalized:
        return ClassificationResult(
            status="error", reason="empty_content", cacheable=False
        )

    # 1. Browser/network error pages (strongest signal)
    match = _pattern_match(normalized, _ERROR_PATTERNS)
    if match:
        return ClassificationResult(
            status="error", reason=f"error_page:{match}", cacheable=False
        )

    # 2. Access blocks (Cloudflare, captcha, IP ban)
    match = _pattern_match(normalized, _BLOCK_PATTERNS)
    if match:
        return ClassificationResult(
            status="blocked", reason=f"access_blocked:{match}", cacheable=False
        )

    # 3. Login walls (content gated behind authentication)
    match = _pattern_match(normalized, _LOGIN_WALL_PATTERNS)
    if match:
        return ClassificationResult(
            status="blocked", reason=f"login_wall:{match}", cacheable=False
        )

    # 4. Paywalls
    match = _pattern_match(normalized, _PAYWALL_PATTERNS)
    if match:
        return ClassificationResult(
            status="blocked", reason=f"paywall:{match}", cacheable=False
        )

    # 5. Redirect URLs (page content is just a URL or redirect notice)
    for regex in _REDIRECT_URL_PATTERNS:
        if regex.search(normalized):
            return ClassificationResult(
                status="partial", reason="redirect_only", cacheable=False
            )

    # 6. Non-printable character spam
    bad_char_ratio = _non_printable_ratio(markdown)
    if bad_char_ratio > 0.15:
        return ClassificationResult(
            status="error", reason="garbled_content", cacheable=False
        )

    # 7. Cookie consent boilerplate (entire page is just cookie banner)
    cookie_ratio = _cookie_boilerplate_ratio(normalized)
    if cookie_ratio > 0.4:
        return ClassificationResult(
            status="partial", reason="cookie_boilerplate", cacheable=False
        )

    # 8. Too short to be useful
    words = len(normalized.split())
    if words < 30:
        return ClassificationResult(
            status="partial", reason="too_short", cacheable=False
        )

    return ClassificationResult(status="success", reason=None, cacheable=True)


def classify_quality(markdown: str) -> float:
    """Return a quality score from 0.0 (junk) to 1.0 (good content).

    This is a lightweight heuristic meant to annotate results
    without running expensive ML. Use it to sort/filter content
    in batch_get_content or to warn agents about dubious sources.
    """
    if not markdown or not markdown.strip():
        return 0.0

    normalized = _normalize(markdown)
    words = normalized.split()
    word_count = len(words)

    # Base score from word count (sigmoid-ish)
    if word_count < 30:
        base = word_count / 60.0  # 0.0 → 0.5
    else:
        base = 0.5 + min(0.5, (word_count - 30) / 400.0)  # 0.5 → 1.0

    # Penalties
    penalty = 0.0

    # Penalty: bad chars
    bad_ratio = _non_printable_ratio(markdown)
    penalty += bad_ratio * 0.5

    # Penalty: cookie boilerplate
    cookie_ratio = _cookie_boilerplate_ratio(normalized)
    penalty += cookie_ratio * 0.3

    # Penalty: block/error/login/paywall signals
    for patterns, weight in (
        (_ERROR_PATTERNS, 0.6),
        (_BLOCK_PATTERNS, 0.5),
        (_LOGIN_WALL_PATTERNS, 0.4),
        (_PAYWALL_PATTERNS, 0.4),
    ):
        if _pattern_match(normalized, patterns):
            penalty += weight
            break  # Only one category penalty

    return max(0.0, min(1.0, base - penalty))
