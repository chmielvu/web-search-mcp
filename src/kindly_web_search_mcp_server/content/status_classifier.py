"""Content status classification with extended quality detection.

Detects junk/blocked/error pages before they reach the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

from .artifact import ContentStatus
from .typed_content import strip_jina_frontmatter
from ..utils.observability import emit_observability_event

logger = logging.getLogger(__name__)

_TYPED_SKIP = frozenset({"json", "jsonl", "csv", "tsv", "rss", "atom", "xml"})
_PASSWORD_RE = re.compile(r"""type\s*=\s*['"]password['"]""", re.IGNORECASE)
_EMPTY_BULLET_RE = re.compile(r"^\s*[\*\-]\s*$")
_ADVERTISEMENT_RE = re.compile(r"(?i)^\s*advertisement\s*$")
_NOT_LOADED_RE = re.compile(r"(?i)not yet fully loaded")
_WIN_THRESHOLD = 0.5
_LONG_DOC_WORDS = 150


@dataclass(frozen=True)
class ClassificationResult:
    status: ContentStatus
    reason: str | None
    cacheable: bool
    score: float = 0.0


_BLOCK_PATTERNS: tuple[str, ...] = (
    "access denied",
    "verify you are human",
    "checking your browser",
    "please enable javascript",
    "please turn javascript on",
    "cloudflare",
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
    "this site can’t be reached",
    "this site can't be reached",
    "chrome-error://chromewebdata",
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

_SPA_SHELL_PATTERNS: tuple[str, ...] = (
    "enable javascript",
    "please turn javascript on",
    "loading...",
    "this application requires javascript",
    "# root",
    "# app",
)
_SPA_SHELL_MAX_WORDS = 150

_REDIRECT_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:https?://)?[^\s]+\s*$"),
    re.compile(r"^redirect(?:ing)?\s+to\s+https?://", re.IGNORECASE),
)


def _compile_phrases(phrases: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for phrase in phrases:
        parts = [re.escape(part) for part in phrase.split()]
        compiled.append((phrase, re.compile(r"\s+".join(parts), re.IGNORECASE)))
    return tuple(compiled)


_ERROR_RE = _compile_phrases(_ERROR_PATTERNS)
_BLOCK_RE = _compile_phrases(_BLOCK_PATTERNS)
_LOGIN_RE = _compile_phrases(_LOGIN_WALL_PATTERNS)
_PAYWALL_RE = _compile_phrases(_PAYWALL_PATTERNS)
_SPA_RE = _compile_phrases(_SPA_SHELL_PATTERNS)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _phrase_hits(text: str, compiled: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
    hits: list[str] = []
    for phrase, regex in compiled:
        if regex.search(text):
            hits.append(phrase)
            if len(hits) >= 3:
                break
    return hits


def _header_challenge(headers: Mapping[str, str] | None, word_count: int = 10**9) -> bool:
    if not headers:
        return False
    for key, value in headers.items():
        if key.lower() == "cf-mitigated" and str(value).strip().lower() == "challenge":
            return True
        # Cloudflare-fronted host returning a near-empty render: Turnstile/interstitial
        # that the client rendered without an explicit cf-mitigated header.
        if (
            key.lower() == "server"
            and "cloudflare" in str(value).strip().lower()
            and word_count < 25
        ):
            return True
    return False


def _is_typed_skip(markdown: str, source_type: str | None) -> bool:
    if (source_type or "").lower() in _TYPED_SKIP:
        return True
    body = strip_jina_frontmatter(markdown).lstrip()
    if not (body.startswith("{") or body.startswith("[")):
        return False
    try:
        json.loads(body)
    except Exception:
        return False
    return True


def _non_printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        cp = ord(ch)
        if cp == 0xFFFD:
            bad += 1
        elif cp == 0x00:
            bad += 1
        elif cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):
            bad += 1
    return bad / len(text)


def _cookie_boilerplate_ratio(normalized: str) -> float:
    words = normalized.split()
    if not words:
        return 0.0
    cookie_count = sum(
        1 for w in words if any(indicator in w for indicator in _COOKIE_CONSENT_INDICATORS)
    )
    return cookie_count / len(words)


def _chrome_ratio(markdown: str) -> float:
    lines = markdown.splitlines()
    total = max(len(lines), 1)
    empty_bullet = sum(1 for line in lines if _EMPTY_BULLET_RE.match(line))
    advertisement = sum(1 for line in lines if _ADVERTISEMENT_RE.match(line))
    duplicate_copies = 0
    previous: str | None = None
    run = 0
    for line in lines:
        if line == previous:
            run += 1
            duplicate_copies += 1
        else:
            previous = line
            run = 0
    return (empty_bullet + advertisement + duplicate_copies) / total



def _emit_classification_event(
    result: ClassificationResult,
    markdown: str,
    normalized: str,
    **extra: float | int | str | bool | None,
) -> None:
    emit_observability_event(
        logger,
        "content.status.classified",
        status=result.status,
        reason=result.reason,
        cacheable=result.cacheable,
        markdown_chars=len(markdown),
        word_count=len(normalized.split()),
        **extra,  # type: ignore[arg-type]
    )


def _category_scores(
    markdown: str,
    *,
    http_status: int | None,
    challenge: bool,
) -> dict[str, tuple[float, list[str]]]:
    error_hits = _phrase_hits(markdown, _ERROR_RE)
    block_hits = _phrase_hits(markdown, _BLOCK_RE)
    login_hits = _phrase_hits(markdown, _LOGIN_RE)
    paywall_hits = _phrase_hits(markdown, _PAYWALL_RE)
    error_score = 0.25 * len(error_hits)
    block_score = 0.25 * len(block_hits)
    login_score = 0.25 * len(login_hits)
    paywall_score = 0.25 * len(paywall_hits)
    if http_status in {401, 403}:
        block_score += 0.7
        login_score += 0.7
    if http_status == 429:
        block_score += 0.8
    if challenge:
        block_score += 0.8
    if _PASSWORD_RE.search(markdown):
        login_score += 0.7
    return {
        "error_page": (error_score, error_hits),
        "access_blocked": (block_score, block_hits),
        "login_wall": (login_score, login_hits),
        "paywall": (paywall_score, paywall_hits),
    }


def classify_markdown(
    markdown: str,
    source_type: str | None = None,
    http_status: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> ClassificationResult:
    """Classify extracted page content for quality/block/error signals."""
    normalized = _normalize(markdown)
    if not normalized:
        result = ClassificationResult(status="error", reason="empty_content", cacheable=False)
        _emit_classification_event(result, markdown, normalized)
        return result

    if _is_typed_skip(markdown, source_type):
        result = ClassificationResult(status="success", reason=None, cacheable=True)
        _emit_classification_event(result, markdown, normalized)
        return result

    word_count = len(normalized.split())
    challenge = _header_challenge(headers, word_count)
    scores = _category_scores(markdown, http_status=http_status, challenge=challenge)
    veto = word_count > _LONG_DOC_WORDS and http_status not in {401, 403, 429} and not challenge

    if not veto:
        for name in ("error_page", "access_blocked", "login_wall", "paywall"):
            score, hits = scores[name]
            if score < _WIN_THRESHOLD:
                continue
            phrase = hits[0] if hits else name
            if name == "error_page":
                result = ClassificationResult(
                    status="error", reason=f"error_page:{phrase}", cacheable=False, score=score
                )
            elif name == "access_blocked":
                result = ClassificationResult(
                    status="blocked",
                    reason=f"access_blocked:{phrase}",
                    cacheable=False,
                    score=score,
                )
            elif name == "login_wall":
                result = ClassificationResult(
                    status="blocked", reason=f"login_wall:{phrase}", cacheable=False, score=score
                )
            else:
                result = ClassificationResult(
                    status="blocked", reason=f"paywall:{phrase}", cacheable=False, score=score
                )
            _emit_classification_event(result, markdown, normalized)
            return result

    for regex in _REDIRECT_URL_PATTERNS:
        if regex.search(normalized):
            result = ClassificationResult(status="partial", reason="redirect_only", cacheable=False)
            _emit_classification_event(result, markdown, normalized)
            return result

    bad_char_ratio = _non_printable_ratio(markdown)
    if bad_char_ratio > 0.15:
        result = ClassificationResult(status="error", reason="garbled_content", cacheable=False)
        _emit_classification_event(
            result, markdown, normalized, bad_char_ratio=round(bad_char_ratio, 4)
        )
        return result

    cookie_ratio = _cookie_boilerplate_ratio(normalized)
    if cookie_ratio > 0.4:
        result = ClassificationResult(
            status="partial", reason="cookie_boilerplate", cacheable=False
        )
        _emit_classification_event(
            result, markdown, normalized, cookie_ratio=round(cookie_ratio, 4)
        )
        return result

    spa_hits = _phrase_hits(markdown, _SPA_RE) if word_count < _SPA_SHELL_MAX_WORDS else []
    if spa_hits:
        result = ClassificationResult(
            status="partial", reason=f"spa_shell:{spa_hits[0]}", cacheable=False
        )
        _emit_classification_event(result, markdown, normalized)
        return result

    not_loaded = bool(_NOT_LOADED_RE.search(markdown))
    if _chrome_ratio(markdown) > 0.5 or (not_loaded and word_count < 80):
        result = ClassificationResult(
            status="partial", reason="chrome_boilerplate", cacheable=False
        )
        _emit_classification_event(result, markdown, normalized)
        return result

    if word_count < 80:
        result = ClassificationResult(status="partial", reason="too_short", cacheable=False)
        _emit_classification_event(result, markdown, normalized)
        return result

    result = ClassificationResult(status="success", reason=None, cacheable=True)
    _emit_classification_event(result, markdown, normalized)
    return result


def classify_quality(markdown: str) -> float:
    """Return a quality score from 0.0 (junk) to 1.0 (good content)."""
    if not markdown or not markdown.strip():
        return 0.0

    normalized = _normalize(markdown)
    words = normalized.split()
    word_count = len(words)

    if word_count < 30:
        base = word_count / 60.0
    else:
        base = 0.5 + min(0.5, (word_count - 30) / 400.0)

    penalty = 0.0
    penalty += _non_printable_ratio(markdown) * 0.5
    penalty += _cookie_boilerplate_ratio(normalized) * 0.3
    scores = _category_scores(markdown, http_status=None, challenge=False)
    for name, weight in (
        ("error_page", 0.6),
        ("access_blocked", 0.5),
        ("login_wall", 0.4),
        ("paywall", 0.4),
    ):
        score, hits = scores[name]
        if hits or score >= _WIN_THRESHOLD:
            penalty += weight
            break

    return max(0.0, min(1.0, base - penalty))


def wall_from_classification(
    status: str,
    error: dict[str, object] | None,
    source_type: str | None,
    markdown: str,
) -> dict[str, object] | None:
    """Project access-wall metadata from classification without substring false positives."""
    del status
    classified = classify_markdown(markdown, source_type=source_type)
    if classified.status == "success":
        return None
    code = str((error or {}).get("code") or "")
    reason = classified.reason or ""
    confidence = "high" if classified.score else "medium"
    if reason.startswith("login_wall") or code.startswith("login_wall"):
        return {"kind": "login", "confidence": confidence, "retryable": False}
    if reason.startswith("paywall") or code.startswith("paywall"):
        return {"kind": "paywall", "confidence": confidence, "retryable": False}
    lowered = f"{reason} {code}".lower()
    if (
        reason.startswith("access_blocked")
        or code.startswith("access_blocked")
        or "captcha" in lowered
        or "challenge" in lowered
    ):
        return {"kind": "bot", "confidence": confidence, "retryable": False}
    if reason.startswith("spa_shell"):
        return {"kind": "js_shell", "confidence": "medium", "retryable": True}
    return None
