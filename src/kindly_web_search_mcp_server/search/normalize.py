from __future__ import annotations

from ..heuristics.text_clean import clean_query
from ..utils.url_canonicalize import canonicalize_url  # noqa: F401


def normalize_query(query: str) -> str:
    return clean_query(query)
