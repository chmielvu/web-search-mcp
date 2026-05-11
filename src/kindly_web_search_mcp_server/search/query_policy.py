"""Query policy: bypass vs expand based on precision signal detection.

No intent classification - just detect if query has precision-sensitive literals
that should be preserved verbatim, or if it can benefit from expansion.

This follows industry patterns from LlamaIndex, LangChain, and Perplexity:
- Detect literals (error codes, versions, URLs, quoted strings) → bypass
- Otherwise → expand via LLM with docs/issues angles
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .normalize import normalize_query

# Simplified mode: bypass (preserve literals) or expand (LLM rewrite)
RewriteMode = Literal["bypass", "expand"]

# Search operators that signal precision intent
_SEARCH_OPERATORS = (
    "site:",
    "filetype:",
    "inurl:",
    "intitle:",
    "repo:",
    "path:",
    "is:",
    "after:",
    "before:",
    "language:",
    "ext:",
    "user:",
)

# Patterns that signal precision-sensitive content (should bypass rewrite)
_PRECISION_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),  # URLs
    re.compile(r"\bwww\.", re.IGNORECASE),
    re.compile(r'["`][^"`]{4,}["`]'),  # Quoted strings (4+ chars, double/backtick)
    re.compile(r"'[^']{4,}'"),  # Single-quoted strings (4+ chars)
    re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b"),  # Repo names (owner/repo)
    re.compile(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+"),  # File paths
    re.compile(r"\b\d+(?:\.\d+){1,3}\b"),  # Version numbers (1.2.3) and decimals
    re.compile(r"0x[0-9A-Fa-f]{4,8}"),  # Hex error codes
    re.compile(r"E[A-Z]+[0-9]+"),  # Error codes (E001, EINVAL, EBADF)
    re.compile(r"ERR_[A-Z_]+"),  # Named error constants (ERR_INVALID_DATA)
    re.compile(r"[A-Z][A-Z0-9_]*::[a-z_]+"),  # Method names (Class::method)
    re.compile(r"[A-Z][a-z]+\.[a-z_]+"),  # Function calls (Foo.bar, np.array)
    re.compile(r"[A-Z_][A-Z0-9_]{3,}"),  # Constants (MAX_SIZE, DEFAULT_TIMEOUT)
    re.compile(r"--[A-Za-z0-9_-]+"),  # Long CLI flags (--verbose, --no-cache)
    re.compile(r"-[A-Za-z]+"),  # Short CLI flags (-v, -f, -it)
    re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    ),  # UUIDs
    re.compile(r"\b[0-9a-fA-F]{7,40}\b"),  # Git hashes (7-40 hex chars)
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP addresses
)


class RewritePolicy(BaseModel):
    """Policy for query rewriting.

    Attributes:
        mode: "bypass" (preserve literals) or "expand" (LLM rewrite)
        reason: Explanation for the chosen mode
        must_keep_terms: Exact literals that must survive any rewriting
    """

    mode: RewriteMode
    reason: str
    must_keep_terms: list[str] = Field(default_factory=list)


def _extract_must_keep_terms(query: str) -> list[str]:
    """Extract exact literals that must survive any rewriting.

    Includes:
    - Quoted strings (double, single, backtick)
    - Search operators with their values
    - URLs, repo names, file paths
    - Version numbers
    - Error codes, constants
    - CLI flags, UUIDs, git hashes, IP addresses
    """
    terms: list[str] = []

    # Quoted strings (all quote types)
    terms.extend(
        match.group(1) for match in re.finditer(r'"([^"]+)"', query) if match.group(1)
    )
    terms.extend(
        match.group(1) for match in re.finditer(r"'([^']+)'", query) if match.group(1)
    )
    terms.extend(
        match.group(1) for match in re.finditer(r"`([^`]+)`", query) if match.group(1)
    )

    # Search operators with their values (e.g., "site:github.com" not just "site")
    for operator in _SEARCH_OPERATORS:
        pattern = re.compile(rf"{re.escape(operator)}(\S+)")
        for match in pattern.finditer(query):
            terms.append(match.group(0))  # Full operator+value

    # Precision patterns
    for pattern in _PRECISION_PATTERNS:
        for match in pattern.finditer(query):
            terms.append(match.group(0))

    # Deduplicate with case-insensitive normalization
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        normalized = normalize_query(term)
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            out.append(normalized)

    return out


def _has_precision_signals(query: str, must_keep_terms: list[str]) -> bool:
    """Detect if query has precision-sensitive content that should bypass rewriting.

    Signals:
    - Multiple search operators (advanced query)
    - Any precision pattern match (URLs, versions, error codes)
    - Extracted must_keep terms (quoted strings, etc.)
    """
    # Multiple search operators = precision query
    operator_count = sum(1 for op in _SEARCH_OPERATORS if op in query)
    if operator_count >= 2:
        return True

    # Any precision pattern match
    if any(pattern.search(query) for pattern in _PRECISION_PATTERNS):
        return True

    # Quoted strings or extracted literals
    if must_keep_terms:
        return True

    return False


def classify_search_query(query: str) -> RewritePolicy:
    """Classify query as bypass or expand based on precision signals.

    No intent classification - just detect if query contains literals
    that should be preserved verbatim.

    Args:
        query: Raw query string

    Returns:
        RewritePolicy with bypass/expand mode and must_keep_terms
    """
    normalized = normalize_query(query)
    must_keep_terms = _extract_must_keep_terms(normalized)

    if _has_precision_signals(normalized, must_keep_terms):
        return RewritePolicy(
            mode="bypass",
            reason="Query contains precision-sensitive literals that should be preserved verbatim.",
            must_keep_terms=must_keep_terms,
        )

    return RewritePolicy(
        mode="expand",
        reason="Query can benefit from expansion with docs/issues angles.",
        must_keep_terms=must_keep_terms,
    )
