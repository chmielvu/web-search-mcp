"""Stdlib-first heuristics: query clean/augment and guidance messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .augment import AugmentResult, augment_query_for_provider, specialized_fallback_query
    from .guidance_messages import (
        format_shaping_guidance,
        web_search_empty_guidance,
        web_search_specialized_gap_guidance,
    )
    from .query_features import QueryFeatures, build_query_features
    from .text_clean import clean_query, clean_text_for_llm, repair_unicode

__all__ = [
    "AugmentResult",
    "QueryFeatures",
    "augment_query_for_provider",
    "build_query_features",
    "clean_query",
    "clean_text_for_llm",
    "format_shaping_guidance",
    "repair_unicode",
    "specialized_fallback_query",
    "web_search_empty_guidance",
    "web_search_specialized_gap_guidance",
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
    if name in {
        "AugmentResult",
        "augment_query_for_provider",
        "specialized_fallback_query",
    }:
        from . import augment

        return getattr(augment, name)
    if name in {
        "format_shaping_guidance",
        "web_search_empty_guidance",
        "web_search_specialized_gap_guidance",
    }:
        from . import guidance_messages

        return getattr(guidance_messages, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
