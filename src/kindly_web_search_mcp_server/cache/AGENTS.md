# AGENTS.md - Caching Layer

In-memory LRU query cache + DuckDB page/transcript caches.

## Key Files

| File | Role |
|---|---|
| `exact_lru.py` | In-memory LRU cache backend |
| `query_cache.py` | Exact query cache facade + observability |
| `page_cache.py` / `page_duckdb.py` | DuckDB-backed page content cache |
| `transcript_cache.py` / `transcript_duckdb.py` | DuckDB-backed YouTube transcript cache |
| `observability.py` | Cache event helpers |

## Rules

- `query_cache.py` keeps the server-facing exact-cache API stable.
- `page_cache.py` and `transcript_cache.py` delegate to DuckDB backends.
- Cache misses return `None` — never fabricate fallback data.
- Cache-hit backend indicator is `cache` in tool/cli output.
- `PageDuckDBCache`/`PageCache` is async via `alookup`/`astore` (thread-pool);
  sync `lookup`/`store` kept for tests/CLI.

## Testing

```bash
uv run pytest tests/test_exact_lru_cache.py tests/test_page_cache_duckdb.py
uv run pytest tests/test_cache_observability.py
uv run pytest tests/test_page_duckdb_schema_errors.py
```