"""Input query optimization for search tool ingress.

Strips legacy search-engine operators (``site:``, ``after:``, ``before:``,
``filetype:``) from client queries before they reach search providers.

Provider-specific domain/date filters should be expressed via API parameters
(``include_domains``, ``exclude_domains``, ``after_date``, etc.), not inline
operator syntax.  Leaving operators in the query string degrades retrieval on
semantic/neural search APIs (Exa, Parallel, Tavily) that do not parse them
and may treat the literal text as content tokens.
"""

from __future__ import annotations

import re

__all__ = ["strip_site_operators"]

# Matches ``site:value`` and ``site:"value with spaces"`` (case-insensitive).
# Also handles ``after:``, ``before:``, ``filetype:``/``ext:`` — operators
# that all three AI-search providers advise against embedding in query text.
_OPERATOR_RE = re.compile(
    r"""\b(?:site|after|before|filetype|ext):"""  # operator prefix
    r"""(?:"([^"]*?)"|(\S+))""",  # quoted or bare value
    re.IGNORECASE,
)


def strip_site_operators(query: str) -> tuple[str, list[str]]:
    """Remove search-engine operator tokens from *query*.

    Returns ``(cleaned_query, stripped_operators)`` where *stripped_operators*
    contains the full ``operator:value`` strings that were removed.  If no
    operators are found the original string is returned unchanged.

    Examples::

        >>> strip_site_operators("machine learning site:arxiv.org")
        ('machine learning', ['site:arxiv.org'])

        >>> strip_site_operators('LLM safety site:"nature.com/articles"')
        ('LLM safety', ['site:nature.com/articles'])

        >>> strip_site_operators("plain query")
        ('plain query', [])
    """
    if not query or ":" not in query:
        return query, []

    stripped: list[str] = []
    parts: list[str] = []
    last_end = 0

    for match in _OPERATOR_RE.finditer(query):
        # Preserve text between matches.
        parts.append(query[last_end : match.start()])
        last_end = match.end()

        # Reconstruct the full operator token for the stripped list.
        value = match.group(1) if match.group(1) is not None else match.group(2)
        op_name = match.group(0).split(":")[0]
        stripped.append(f"{op_name}:{value}")

    if not stripped:
        return query, []

    parts.append(query[last_end:])
    cleaned = " ".join("".join(parts).split()).strip()
    return cleaned, stripped
