# AGENTS.md - Documentation

This directory is the human-readable reference layer for the repo.

## Current Files

- `DuckDB_schema.md` - canonical DuckDB schema for analytics/search quality
- `logdb_schema.md` - process-log schema reference
- `crawl4ai-research.md` - notes for remote Crawl4AI crawling and sitemap work

## Use This Directory For

- Schemas that should stay aligned with `analytics/` and `observability/`
- Architecture notes that are useful to humans but too stable for code comments
- Research writeups that explain why a subsystem changed

## Editing Rules

- Keep docs in sync with the runtime behavior they describe
- If analytics tables, views, or report shapes change, update the schema docs
- If crawl/extraction behavior changes, update `crawl4ai-research.md`
- Prefer concrete file references over prose-only descriptions
