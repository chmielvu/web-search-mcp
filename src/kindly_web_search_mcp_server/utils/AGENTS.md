<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Utils

Cross-cutting helpers shared across the codebase.

## Key Files

| File | Role |
|---|---|
| `http_client.py` | Shared HTTP client lifecycle |
| `logging.py` | Logging configuration |
| `async_helpers.py` | Async helpers and task wrappers |
| `background_tasks.py` | Fire-and-forget task tracking |
| `singleflight.py` | Request coalescing |
| `url_canonicalize.py` | URL canonicalization + `extract_domain_from_url` |
| `paths.py` | Path helpers and defaults |
| `observability.py` | Observability event helpers |
| `public_output.py` | Public response serialization |
| `diagnostics.py` | Diagnostics helpers and env masking |
| `sqlite_log_handler.py` | SQLite (WAL) process-log handler with FTS5 |
| `snippet_normalizer.py` | Snippet cleanup helpers |
| `structured_logging.py` | Structured logging helpers |

## Rules

- Keep cross-cutting helpers out of feature packages.
- Centralize logging, HTTP client reuse, observability, output shaping.
- Tool lifecycle events are normalized and routed through `utils/observability.py` into typed analytics `tool_calls`; do not resurrect the removed generic `search_events` sink.
- Provide small reusable primitives (singleflight, snippet cleanup, URL canonicalization).
-- `BatchSQLiteLogHandler.close()` flushes buffered records before marking the handler closed, preserving the final batch on shutdown.
- Process-log TTL cleanup compares `julianday(recorded_at)` with the cutoff in days — `datetime()` returns NULL for the stored ISO-8601 text and silently disables the DELETE.
- The external-content FTS5 index is backfilled at schema creation and fed per flush via `INSERT ... RETURNING rowid`; external-content tables never auto-populate.
- `install_process_logging()` uses `TracebackPreservingQueueHandler`, which keeps formatted exception text across the queue (stdlib `QueueHandler.prepare()` strips `exc_info`/`exc_text`).

## Testing

```bash
uv run pytest tests/test_async_helpers.py tests/test_scripts_env_loader.py
```