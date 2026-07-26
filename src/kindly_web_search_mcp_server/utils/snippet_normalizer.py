"""Normalize search result snippets for clean MCP output.

Strips HTML tags, navigation chrome, base64 blobs, excessive whitespace,
and caps snippet length to a configurable maximum.
"""

from __future__ import annotations

import re

from ..heuristics.text_clean import clean_text_for_llm

# Hard cap on snippet characters (prevents context-budget blowouts)
MAX_SNIPPET_LENGTH = 500

# Patterns to strip entirely
_STRIP_PATTERNS: list[re.Pattern[str]] = [
    # HTML tags (including img with base64 data URIs)
    re.compile(r"<[^>]+>", re.DOTALL),
    # Base64 data URIs (can be enormous)
    re.compile(r"data:[a-zA-Z/+]+;base64,[A-Za-z0-9+/=]{50,}"),
    # Navigation chrome common in Reddit / forum results
    re.compile(
        r"(?:\[?\s*(?:Sign\s*Up|Log\s*In|Join|Subscribe|Download)\s*\]?)",
        re.IGNORECASE,
    ),
    # Bare URLs inside snippets (not useful as content)
    re.compile(r"https?://\S{80,}"),
]

# Collapse runs of whitespace / newlines
_MULTI_WHITESPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINES = re.compile(r"\n{3,}")


def normalize_snippet(text: str, *, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    """Clean a raw snippet for MCP tool output.

    1. Strip HTML tags and base64 data URIs
    2. Remove navigation chrome (Sign Up, Log In, etc.)
    3. Collapse whitespace
    4. Truncate to *max_length* with ellipsis
    """
    if not text:
        return ""

    cleaned = clean_text_for_llm(text, role="snippet")
    for pattern in _STRIP_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    cleaned = _MULTI_WHITESPACE.sub(" ", cleaned)
    cleaned = _MULTI_NEWLINES.sub("\n\n", cleaned)
    cleaned = cleaned.strip()

    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"

    return cleaned
