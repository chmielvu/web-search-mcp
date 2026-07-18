# Firecrawl Cloud Batch Scrape Integration — Refined Plan

## Context

Replace Jina Reader as the first backend used by `batch_get_content` with the Firecrawl Cloud batch-scrape API. Firecrawl is called once per batch via `firecrawl-py`, returns Markdown+metadata for every URL it covers, and any URL it fails to cover (plus any batch-level failure) falls back to the per-URL extraction pipeline in the order Crawl4AI → local BS4 → Camoufox. Jina is skipped for batch requests. Authentication uses `FIRECRAWL_API_KEY`.

## Approach

### 1. Use the official `firecrawl-py` SDK

- Import: `from firecrawl.v2.client_async import AsyncFirecrawlClient` (the installed `firecrawl-py` 4.32.1 exposes v2 batch methods on this class).
- Single waiter call: `await client.batch_scrape(urls, formats=[...], poll_interval=..., timeout=...)`.
- The SDK starts the job, polls to completion, auto-paginates `next` URLs, and normalizes camelCase response fields to snake_case.
- Drop the original plan's bespoke `httpx.AsyncClient`, manual start/wait/pagination, and `FirecrawlClientError`.

### 2. Settings

Add next to Crawl4AI settings in `src/kindly_web_search_mcp_server/settings.py`:

- `firecrawl_api_key`
- `firecrawl_api_url` (default `https://api.firecrawl.dev`)
- `firecrawl_timeout_seconds` (per-HTTP-request timeout)
- `firecrawl_poll_interval_seconds`
- `firecrawl_max_poll_seconds`

No `FIRECRAWL_BATCH_MAX_URLS`; Firecrawl does not publish a hard limit.

### 3. Client singleton

`src/kindly_web_search_mcp_server/content/firecrawl_client.py`:

- `get_firecrawl_client() -> AsyncFirecrawl | None`
- `close_firecrawl_client()`

### 4. Batch stage

`src/kindly_web_search_mcp_server/content/firecrawl_stage.py`:

- `run_firecrawl_batch(urls, *, options, batch_params) -> dict[str, ContentArtifact]`.
- `formats = ["markdown"]` plus `"links"` when `options.include_links`.
- Call `batch_scrape` with `only_main_content=True`, `ignore_invalid_urls=True`.
- Map documents using `metadata.source_url` / `metadata.url` (snake_case via SDK conversion).
- Empty markdown → error artifact; missing URLs → fallback; exceptions → fallback all.

### 5. Per-URL fallback without Jina

- Add `skip_jina: bool = False` to `fetch_content_artifact`.
- When true, skip the Jina Reader stage and start Tier 2 with Crawl4AI.

### 6. Batch orchestrator

- Pre-fetch via `run_firecrawl_batch` before the per-URL loop.
- Share result-building logic via `_artifact_to_result`.
- Firecrawl-covered URLs added to `processed_urls`.
- Fallback URLs use `fetch_content_artifact(..., skip_jina=True)`.

### 7. Lifecycle cleanup

- Add `close_firecrawl_client()` to `tools/_helpers.py::_app_lifespan` and `cli/runtime.py::_runner`.
- Also wire existing `close_crawl4ai_client()` and `close_camoufox_client()` in the same places.

### 8. Dependency

- Add `firecrawl-py>=4.32.1,<5` to `pyproject.toml`.

### 9. Tests

- `tests/test_firecrawl_stage.py` mocks `AsyncFirecrawl`.
- Tests for `skip_jina` and orchestrator fallback.

## Verification

- `pytest tests/test_firecrawl_stage.py tests/test_content_*.py`
- `ruff check src/ tests/`
- Optional live MCP test.

## Sources

- https://docs.firecrawl.dev/features/batch-scrape
- https://docs.firecrawl.dev/agent-source-of-truth/python
- https://raw.githubusercontent.com/firecrawl/firecrawl/main/apps/python-sdk/example.py
