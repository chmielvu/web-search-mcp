"""Cache package.

Provides:
- exact query cache (LRU)
- page cache (SQLite WAL, separate file)
- transcript cache (SQLite WAL, separate file)
"""

from .code_search import (
    CodeSearchCache,
    build_search_cache_key,
    get_code_search_cache,
    is_immutable_revision,
    reset_code_search_cache,
)
from .exact_lru import ExactLRUCache
from .page_cache import PAGE_CACHE_DEFAULT_TTL_SECONDS, PageCache, get_page_cache
from .query_cache import (
    QUERY_CACHE_DEFAULT_MAX_ENTRIES,
    QUERY_CACHE_DEFAULT_TTL_SECONDS,
    ExactQueryCache,
    get_query_cache,
    provider_cache_key,
)
from .transcript_cache import TranscriptCache, get_transcript_cache

__all__ = [
    "PAGE_CACHE_DEFAULT_TTL_SECONDS",
    "QUERY_CACHE_DEFAULT_MAX_ENTRIES",
    "QUERY_CACHE_DEFAULT_TTL_SECONDS",
    # Code-search cache tiers
    "CodeSearchCache",
    "ExactLRUCache",
    # Exact query cache (LRU)
    "ExactQueryCache",
    # Page cache (SQLite)
    "PageCache",
    # Transcript cache (SQLite)
    "TranscriptCache",
    "build_search_cache_key",
    "get_code_search_cache",
    "get_page_cache",
    "get_query_cache",
    "get_transcript_cache",
    "is_immutable_revision",
    "provider_cache_key",
    "reset_code_search_cache",
]
