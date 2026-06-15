"""Cache package.

Provides:
- exact query cache (LRU)
- page cache (DuckDB, separate file)
- transcript cache (DuckDB, separate file)
"""

from .query_cache import (
    ExactQueryCache,
    QUERY_CACHE_DEFAULT_MAX_ENTRIES,
    QUERY_CACHE_DEFAULT_TTL_SECONDS,
    get_query_cache,
    provider_cache_key,
)
from .exact_lru import ExactLRUCache
from .page_cache import PageCache, get_page_cache, PAGE_CACHE_DEFAULT_TTL_SECONDS
from .transcript_cache import TranscriptCache, get_transcript_cache

__all__ = [
    # Exact query cache (LRU)
    "ExactQueryCache",
    "ExactLRUCache",
    "get_query_cache",
    "provider_cache_key",
    "QUERY_CACHE_DEFAULT_MAX_ENTRIES",
    "QUERY_CACHE_DEFAULT_TTL_SECONDS",
    # Page cache (DuckDB)
    "PageCache",
    "get_page_cache",
    "PAGE_CACHE_DEFAULT_TTL_SECONDS",
    # Transcript cache (DuckDB)
    "TranscriptCache",
    "get_transcript_cache",
]
