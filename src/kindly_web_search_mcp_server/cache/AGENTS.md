# AGENTS.md - Caching Layer

This directory implements the multi-layer caching system for the search pipeline.

## Structure

cache/
|-- __init__.py              # Cache exports and factory functions
|-- query_cache.py           # Exact query cache (SQLite-backed, deterministic)
|-- semantic_cache.py        # LanceDB-backed semantic similarity cache (embedding-based fuzzy match)
|-- page_cache.py            # URL -> page_content cache
-- content_type.py          # Content type detection (removed in recent refactor)

## Cache Layers

### Query Cache (query_cache.py)
- Exact match on normalized query strings
- SQLite-backed for persistence
- Deterministic, fast lookup
- Used for identical repeat queries

### Semantic Cache (semantic_cache.py)
- Embedding-based fuzzy matching
- LanceDB vector store for similarity search
- Finds semantically similar queries
- Configurable similarity threshold

### Page Cache (page_cache.py)
- URL -> extracted page content mapping
- Avoids re-fetching and re-extracting same URLs
- TTL-based expiration

## Key Patterns

### Cache Invalidation
- Query cache: manual or TTL-based
- Semantic cache: embedding drift detection
- Page cache: TTL + content hash comparison

## Testing
pytest tests/test_cache_observability.py -v

## Conventions
- All caches implement async get/set interfaces
- Cache keys are deterministic hashes
- Errors are caught and logged, cache misses return None
