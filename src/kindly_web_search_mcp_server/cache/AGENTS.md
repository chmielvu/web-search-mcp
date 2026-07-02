# AGENTS.md - Caching Layer

This directory contains the cache layers used by search and content tools.

## Current Structure

cache/
|-- exact_lru.py             # Exact query cache backend
|-- query_cache.py           # Exact query cache facade / observability
|-- page_duckdb.py           # DuckDB-backed page cache
|-- page_cache.py            # Page cache facade
|-- transcript_duckdb.py     # DuckDB-backed transcript cache
|-- transcript_cache.py      # Transcript cache facade
└── observability.py         # Cache event helpers

## Cache Layers

- Exact query cache: in-memory LRU for identical repeat queries
- Page cache: DuckDB-backed cache for extracted page content
- Transcript cache: DuckDB-backed cache for YouTube transcript payloads

## Current Behavior

- `query_cache.py` keeps the server-facing exact-cache API stable
- `page_cache.py` and `transcript_cache.py` delegate to DuckDB backends
- Cache misses are expected and should return `None` rather than fabricating
  fallback data

## Testing

- `python -m pytest tests/test_cache_observability.py`
- `python -m pytest tests/test_exact_lru_cache.py tests/test_page_cache_duckdb.py`
