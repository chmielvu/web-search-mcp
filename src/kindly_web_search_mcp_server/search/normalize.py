from __future__ import annotations

from ..utils.url_canonicalize import canonicalize_url  # noqa: F401


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split())
