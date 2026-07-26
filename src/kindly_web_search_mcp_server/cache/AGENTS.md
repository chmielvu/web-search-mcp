# AGENTS.md - Caching Layer

In-memory LRU query cache + DuckDB page/transcript caches.

## Key Files

| File | Role |
|---|---|
| `exact_lru.py` | In-memory LRU cache backend |
| `query_cache.py` | Exact query cache facade + observability |
| `page_cache.py` / `page_sqlite.py` | SQLite (WAL)-backed page content cache |
| `transcript_cache.py` / `transcript_sqlite.py` | SQLite (WAL)-backed YouTube transcript cache with FTS5 |
| `observability.py` | Cache event helpers |

## Rules

- `query_cache.py` keeps the server-facing exact-cache API stable.
- `page_cache.py` and `transcript_cache.py` delegate to SQLite WAL backends.
- Cache misses return `None` — never fabricate fallback data.
- Cache-hit backend indicator is `cache` in tool/cli output.
- `PageSQLiteCache`/`PageCache` is async via `alookup`/`astore` (thread-pool);
  sync `lookup`/`store` kept for tests/CLI.

## Testing

```bash
uv run pytest tests/test_exact_lru_cache.py tests/test_page_cache_duckdb.py tests/test_transcript_cache.py
uv run pytest tests/test_cache_observability.py
```