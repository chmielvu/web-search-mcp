"""Single-source post-processing for web-search responses.

Consolidates the domain-boost reordering that previously lived in
``tools/_helpers.py`` so ``search/ranking.py::rank_and_finalize`` owns the
final page order before evidence and citation construction.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from typing import TypeVar
from urllib.parse import urlparse

T = TypeVar("T")


def _result_link[T](result: T) -> str:
    """Duck-typed link accessor: pydantic models and plain dicts both work."""
    if isinstance(result, dict):
        return str(result.get("link") or "")
    return str(getattr(result, "link", None) or "")


def _url_matches_domain(url: str, pattern: str) -> bool:
    """Check if URL matches domain pattern (supports wildcards, subdomains, and paths)."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().replace("www.", "")
        pathname = parsed.path.lower()

        p = pattern.strip().lower()
        if p.startswith("site:"):
            p = p[5:].strip()
        if p.startswith("https://"):
            p = p[8:].strip()
        elif p.startswith("http://"):
            p = p[7:].strip()
        if p.startswith("www."):
            p = p[4:].strip()

        if "/" in p:
            pat_domain, *pat_parts = p.split("/")
            pat_path = "/" + "/".join(pat_parts)
            if pat_path.endswith("/") and len(pat_path) > 1:
                pat_path = pat_path.rstrip("/")
            domain_match = (
                (fnmatch.fnmatch(hostname, pat_domain) or hostname == pat_domain.lstrip("*."))
                if any(c in pat_domain for c in ("*", "?"))
                else (hostname == pat_domain or hostname.endswith(f".{pat_domain}"))
            )
            path_match = (
                fnmatch.fnmatch(pathname, pat_path)
                if any(c in pat_path for c in ("*", "?"))
                else pathname.startswith(pat_path)
            )
            return domain_match and path_match

        if any(c in p for c in ("*", "?")):
            return fnmatch.fnmatch(hostname, p) or hostname == p.lstrip("*.")
        return hostname == p or hostname.endswith(f".{p}")
    except (ValueError, TypeError, AttributeError):
        return False


def apply_domain_boost[T](
    results: Sequence[T],
    domain_boost: tuple[str, ...] | list[str] | None,
) -> list[T]:
    """Reorder results so domain-boosted entries come first, preserving relative order.

    Args:
        results: Search results (pydantic models or dicts with a ``link`` key).
        domain_boost: Domains to boost (move to front, preserving relative order).

    Returns:
        Boosted results list.
    """
    if not domain_boost:
        return list(results)
    patterns = [p for p in domain_boost if p]
    if not patterns:
        return list(results)

    def _boosted(result: T) -> bool:
        link = _result_link(result)
        return bool(link and any(_url_matches_domain(link, p) for p in patterns))

    boosted: list[T] = []
    normal: list[T] = []
    for r in results:
        if _boosted(r):
            boosted.append(r)
        else:
            normal.append(r)
    return boosted + normal
