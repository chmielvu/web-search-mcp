"""Cache package.

Provides:
- exact query cache (LRU)
- page cache (DuckDB, separate file)
- content type classification (kept for potential future use)
"""

from .content_type import (
    ADAPTIVE_TTL,
    ADAPTIVE_TTL_SECONDS,
    ContentType,
    classify_content_type,
)
from .query_cache import (
    ExactQueryCache,
    QUERY_CACHE_DEFAULT_MAX_ENTRIES,
    QUERY_CACHE_DEFAULT_TTL_SECONDS,
    get_query_cache,
    provider_cache_key,
)
from .exact_lru import ExactLRUCache
from .page_cache import PageCache, get_page_cache, PAGE_CACHE_DEFAULT_TTL_SECONDS

__all__ = [
    # Content type classification (retained module; no longer used by semantic cache)
    "ContentType",
    "classify_content_type",
    "ADAPTIVE_TTL",
    "ADAPTIVE_TTL_SECONDS",
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
]
