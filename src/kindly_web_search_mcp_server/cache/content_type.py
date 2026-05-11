"""Content type classification and adaptive TTL configuration."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum


class ContentType(StrEnum):
    """Classification types for cached content."""

    TECHNICAL = "technical"
    NEWS = "news"
    FAQ = "faq"
    GENERAL = "general"


# Adaptive TTL map (timedelta for flexibility)
ADAPTIVE_TTL: dict[ContentType, timedelta] = {
    ContentType.NEWS: timedelta(minutes=15),  # 15 minutes
    ContentType.TECHNICAL: timedelta(hours=24),  # 24 hours
    ContentType.FAQ: timedelta(days=7),  # 7 days
    ContentType.GENERAL: timedelta(hours=12),  # 12 hours
}


# Also provide seconds for backward compatibility
ADAPTIVE_TTL_SECONDS: dict[ContentType, int] = {
    ContentType.NEWS: 15 * 60,  # 15 minutes
    ContentType.TECHNICAL: 24 * 3600,  # 24 hours
    ContentType.FAQ: 7 * 24 * 3600,  # 7 days
    ContentType.GENERAL: 12 * 3600,  # 12 hours
}


# Keywords for heuristic classification
_NEWS_KEYWORDS = {
    "breaking", "latest", "today", "yesterday", "news", "update",
    "announcement", "release", "published", "reported", "headline",
    "stories", "coverage", "current", "recent", "happening", "now"
}

_TECHNICAL_KEYWORDS = {
    "how", "implement", "code", "function", "method", "class", "api",
    "library", "framework", "programming", "debug", "error", "fix",
    "tutorial", "guide", "example", "syntax", "config", "install",
    "setup", "python", "javascript", "typescript", "java", "sql"
}

_FAQ_KEYWORDS = {
    "what", "why", "when", "where", "who", "which", "faq", "question",
    "answer", "help", "support", "solve", "issue", "problem", "troubleshoot",
    "how do i", "how to", "can i", "does", "is there", "mean", "difference",
    "compare", "vs", "versus", "best", "should", "recommend"
}


def classify_content_type(query: str) -> ContentType:
    """Classify query content type using keyword heuristics.

    Args:
        query: The search query text.

    Returns:
        The classified ContentType.
    """
    query_lower = query.lower()
    words = set(query_lower.split())

    # Check for news keywords
    if _NEWS_KEYWORDS.intersection(words):
        return ContentType.NEWS

    # Check for technical keywords
    if _TECHNICAL_KEYWORDS.intersection(words):
        return ContentType.TECHNICAL

    # Check for FAQ keywords
    if _FAQ_KEYWORDS.intersection(words):
        return ContentType.FAQ

    # Default to general
    return ContentType.GENERAL