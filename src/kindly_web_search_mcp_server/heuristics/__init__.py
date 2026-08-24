"""Stdlib-first heuristics: query shaping, cleaning, and guidance messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lang_detect import detect_lang
    from .query_features import QueryFeatures, build_query_features
    from .shaping import AugmentResult, extract_search_ops, shape_for_branch
    from .text_clean import clean_query, clean_text_for_llm, repair_unicode
    from .text_segment import (
        MAX_TOKEN_LEN,
        MIN_TOKEN_LEN,
        is_eligible_token,
        segment_query,
    )
    from .understanding_fallback import FallbackUnderstanding, resolve_fallback_understanding

__all__ = [
    "AugmentResult",
    "FallbackUnderstanding",
    "MAX_TOKEN_LEN",
    "MIN_TOKEN_LEN",
    "QueryFeatures",
    "build_query_features",
    "clean_query",
    "clean_text_for_llm",
    "detect_lang",
    "extract_search_ops",
    "is_eligible_token",
    "repair_unicode",
    "resolve_fallback_understanding",
    "segment_query",
    "shape_for_branch",
]


def __getattr__(name: str):
    if name in {
        "repair_unicode",
        "clean_query",
        "clean_text_for_llm",
    }:
        from . import text_clean

        return getattr(text_clean, name)
    if name in {"QueryFeatures", "build_query_features"}:
        from . import query_features

        return getattr(query_features, name)
    if name in {"AugmentResult", "extract_search_ops", "shape_for_branch"}:
        from . import shaping

        return getattr(shaping, name)
    if name in {"detect_lang"}:
        from . import lang_detect

        return getattr(lang_detect, name)
    if name in {"segment_query", "MIN_TOKEN_LEN", "MAX_TOKEN_LEN", "is_eligible_token"}:
        from . import text_segment

        return getattr(text_segment, name)
    if name in {"FallbackUnderstanding", "resolve_fallback_understanding"}:
        from . import understanding_fallback

        return getattr(understanding_fallback, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
