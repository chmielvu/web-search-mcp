"""Semantic cache module for web-search-mcp.

Provides LanceDB-backed semantic caching with hybrid search,
adaptive TTL based on content type, and embedding-based lookup.

Also provides exact query cache and page cache for deterministic caching.
"""

from .content_type import ADAPTIVE_TTL, ADAPTIVE_TTL_SECONDS, ContentType, classify_content_type
from .schema import SEMANTIC_CACHE_SCHEMA, SEMANTIC_CACHE_TABLE_NAME
from .semantic_cache import get_semantic_cache, set_semantic_cache
from .store import SemanticCacheStore
from .query_cache import (
    ExactQueryCache,
    QUERY_CACHE_DEFAULT_TTL_SECONDS,
    get_query_cache,
    provider_cache_key,
)
from .page_cache import PageCache, get_page_cache, PAGE_CACHE_DEFAULT_TTL_SECONDS

__all__ = [
    # Schema
    "SEMANTIC_CACHE_SCHEMA",
    "SEMANTIC_CACHE_TABLE_NAME",
    # Semantic cache store
    "SemanticCacheStore",
    # Semantic cache operations
    "get_semantic_cache",
    "set_semantic_cache",
    # Content type classification
    "ContentType",
    "classify_content_type",
    "ADAPTIVE_TTL",
    "ADAPTIVE_TTL_SECONDS",
    # Exact query cache
    "ExactQueryCache",
    "get_query_cache",
    "provider_cache_key",
    "QUERY_CACHE_DEFAULT_TTL_SECONDS",
    # Page cache
    "PageCache",
    "get_page_cache",
    "PAGE_CACHE_DEFAULT_TTL_SECONDS",
]
