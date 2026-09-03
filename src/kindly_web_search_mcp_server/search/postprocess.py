"""Single-source post-processing for web-search responses.

Consolidates the domain-boost reordering that previously lived in
``tools/_helpers.py::_apply_domain_filters`` so the service layer owns
every post-rank transformation (temporal filter + domain boost).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def _result_link(result: T) -> str:
    """Duck-typed link accessor: pydantic models and plain dicts both work."""
    if isinstance(result, dict):
        return str(result.get("link") or "")
    return str(getattr(result, "link", None) or "")


def _url_matches_domain(url: str, pattern: str) -> bool:
    """Check if URL matches domain pattern (supports wildcards, subdomains, and paths)."""
    try:
        from fnmatch import fnmatch
        from urllib.parse import urlparse

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
    except Exception:
        return False


def apply_domain_boost(
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
        return any(_url_matches_domain(_result_link(result), p) for p in patterns)

    boosted = [r for r in results if _boosted(r)]
    normal = [r for r in results if not _boosted(r)]
    return boosted + normal
