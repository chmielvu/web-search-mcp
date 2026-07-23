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
| `duckdb_log_handler.py` | DuckDB process-log handler |
| `snippet_normalizer.py` | Snippet cleanup helpers |
| `structured_logging.py` | Structured logging helpers |

## Rules

- Keep cross-cutting helpers out of feature packages.
- Centralize logging, HTTP client reuse, observability, output shaping.
- Provide small reusable primitives (singleflight, snippet cleanup, URL canonicalization).

## Testing

```bash
uv run pytest tests/test_async_helpers.py tests/test_scripts_env_loader.py
```