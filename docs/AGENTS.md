<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Documentation

Human-readable reference layer for the repo.

## Current Files

| File | Purpose |
|---|---|
| `DuckDB_schema.md` | Canonical DuckDB schema for analytics/search quality |
| `logdb_schema.md` | Process-log schema reference |
| `crawl4ai-research.md` | Notes for remote Crawl4AI crawling and sitemap |

## Rules

- Keep docs in sync with the runtime behavior they describe.
- If analytics tables, views, or report shapes change, update `DuckDB_schema.md`.
- If crawl/extraction behavior changes, update `crawl4ai-research.md`.
- Prefer concrete file references over prose-only descriptions.
- Document schemas that should stay aligned with `analytics/` and `observability/`.
