from __future__ import annotations

from ..utils.text_clean import clean_query


def normalize_query(query: str) -> str:
    return clean_query(query)
