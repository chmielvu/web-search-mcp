# Code Review Report — `kindly_web_search_mcp_server`

**Date:** 2026-06-11

---

## Critical Bugs

### 1. `search/query_execution.py:144` — Semaphore defeats its own purpose (HIGH IMPORTANCE)

**Current code:**
```python
semaphore = asyncio.Semaphore(max(2, len(paid_providers)))
```

**Intent:** Rate-limit concurrent calls to paid SERP providers (Brave, Google CSE) to avoid quota exhaustion.

**Actual behavior:** `Semaphore(max(2, N))` with N tasks gives N slots — all tasks run concurrently. The semaphore never blocks anything when there are 3+ paid providers.

**Impact:** No rate limiting on paid SERP APIs. Fire-and-forget across all paid providers.

**Fix:** Use a fixed semaphore value (e.g., `Semaphore(2)`) that caps concurrent paid provider calls regardless of how many providers are configured. The semaphore should be a module-level constant or env-configurable.

---

### 2. `server.py:1463` — Gemini judge uses wrong key name

Gemini search results use `"uri"` key for the link field while Perplexity and Grok use `"url"`. Judge evaluation always gets empty `link` for Gemini results.

---

### 3. `errors.py:56-59` — Truthiness check drops valid zero values

```python
if self.status_code:   # drops status_code=0
if self.retry_after:   # drops retry_after=0 (retry immediately)
```

Should use `is not None` checks instead.

---

### 4. `agent/mcp.py:63` — Double-counted telemetry on failures

`record_mcp_tool_call(success=True)` called eagerly before the operation, then overwritten on error. Failures increment both success=True and success=False metrics.

---

### 5. `agent/mcp.py:72` — `ctx.session_id` may not exist

Direct attribute access without `getattr` guard. Other parts of the codebase (e.g., `server.py`) use safe `_resolve_session_id` with fallbacks.

---

### 6. `middleware/session_tracking.py:28` — `id()` as session ID is unstable

```python
return f"local_context:{id(fastmcp_context)}"
```

Memory address reuse causes cross-request state pollution in expensive-tool-protection middleware.

---

### 7. `server.py:2008` — Cache mutation on lookup

```python
exact_cached["query"] = query  # mutates the cached dict in-place
```

Concurrent requests share and corrupt the same cached dict object.

---

### 8. `server.py:556` — Potential `AttributeError` on `gemini_api_key`

```python
settings.gemini_api_key.strip()
```

No None check. Other env vars use safe `.get(..., "").strip()` pattern.

---

### 9. `search/pipeline.py:395-396` — `session_state` may be None

`get_session_state_store().get(session_id)` can return None, then `.last_intent = context.intent` raises `AttributeError`.

---

### 10. `cache/page_duckdb.py` — No upsert, duplicate rows accumulate

Same URL stored twice creates unbounded disk growth. `lookup` returns only `LIMIT 1` so stale rows pile up forever.

---

### 11. `content/stackexchange.py:246-269` — Unbounded pagination

`while True` loop with no `max_pages` guard. Thousands of answers = many sequential API calls.

---

## High-Priority Bugs

| # | Location | Issue |
|---|----------|-------|
| 12 | `telemetry.py:508` | Hardcoded `telemetry.sdk.version: "1.20.0"` — false metadata in all traces. |
| 13 | `telemetry.py:543` | `Settings()` instantiated instead of using singleton — Langfuse credentials may mismatch runtime config. |
| 14 | `search/pipeline.py:163-165` | HTTP client read timeout 20s too tight — Grok expects 60s, SearXNG can be longer. |
| 15 | `search/jina.py:90` | Query not URL-encoded — special chars produce malformed URLs. |
| 16 | `search/pollinations.py:107` | Creates new `httpx.AsyncClient` per request — ignores shared pipeline client, no connection pooling. |
| 17 | `content/safe_fetch.py:130` | Synchronous DNS resolution blocks event loop — `socket.getaddrinfo` without timeout. |
| 18 | `agent/runner.py:90` | Path traversal via env var — `open(raw)` with user-controlled config path, no validation. |
| 19 | `rerank/jina.py:17-21` | Module-level `AsyncClient` shared across event loops — stale client after server restart. |
| 20 | `search/query_execution.py:126-131` | `asyncio.gather` result double-awaited — unreachable `else` branch. |
| 21 | `search/query_execution.py:144` | (See Critical Bug #1 above) |
| 22 | `agent/content_tools.py:77` | No timeout wrapper around `fetch_content_artifact` — slow pages block agent indefinitely. |
| 23 | `search/pipeline.py:322-333` | Shadow-mode reranking captures post-rerank list instead of pre-rerank — comparison is meaningless. |

---

## Dead Code

| # | Location | Issue |
|---|----------|-------|
| 24 | `server.py:33` | `import argparse` only used in one function. |
| 25 | `server.py:36` | `import json` at module level, only used once in `academic_search`. |
| 26 | `agent/runner.py:195` | `warnings = []` immediately overwritten on line 210. |
| 27 | `agent/model.py:16-17` | `self.runnable` and `self.fallbacks` assigned but never read. |
| 28 | `telemetry.py:1491-1525` | `record_query_rewrite(duration_seconds)` — all callers pass None, histogram unreachable. |
| 29 | `telemetry.py:1651-1684` | `record_gemini_search`/`record_perplexity_search` accept `duration_seconds` but never record it. |
| 30 | `telemetry.py:1298-1336` | `record_agentic_research` — all parameters silently ignored. |
| 31 | `utils/logging.py:7-38` | Entire module effectively dead — `configure_structlog` replaces its config. |
| 32 | `cache/content_type.py` | Entire module unused — retained from removed semantic cache. |
| 33 | `cache/query_cache.py:53,58` | `db_path` parameter accepted but never used (pure in-memory LRU). |
| 34 | `scrape/fetch.py` | `fetch_url` appears unused — pipeline uses `safe_fetch_url`. |
| 35 | `content/arxiv.py:136-138` | `_iter_page_indices` generator never called. |
| 36 | `rerank/observability.py:78-84` | `emit_rerank_policy_decision` never called. |
| 37 | `scrape/universal_html.py:81-97` | Local `_resolve_browser_executable_path` defined but never called. |
| 38 | `settings.py:539` | `ab_shadow_mode_default` — setting exists but is never read anywhere. |
| 39 | `middleware/query_guidance.py:194` | `del data` — unnecessary parameter deletion. |
| 40 | `search/merge.py:140` | Module-level `tracer` shadowed by local variable — never used. |
| 41 | `search/query_execution.py:128-131` | `else` branch after `asyncio.gather` — unreachable code. |

---

## Quick Wins

| # | Location | Issue |
|---|----------|-------|
| 42 | `runner.py:189` | `__import__("json")` — use standard module-level import. |
| 43 | `content_tools.py:68` | `__import__("logging")` — same issue. |
| 44 | `server.py:1462-1704` | `type('obj', ...)` — use `types.SimpleNamespace` instead. |
| 45 | `server.py` (2332 lines) | God Object — 10+ tools, 7+ resources, CLI, cache helpers all in one file. |
| 46 | `errors.py:79` | Docstring documents `url` parameter that doesn't exist. |
| 47 | `errors.py:127-138` | Error classification by string matching on `str(error)` — fragile. |
| 48 | `session_tracking.py:90` | `cleanup_expired_sessions()` scans ALL sessions on every tool call. |
| 49 | `wiring.py:56` | `load_experiments()` re-parses YAML from disk on every search run. |
| 50 | `duckdb_store.py:279-861` | ~580 lines of duplicated insert boilerplate. Extract generic helper. |
| 51 | `duckdb_store.py:116-234` | Schema migration runs on every `append_event` call — add one-shot flag. |
| 52 | `content/github_issues.py` + `github_discussions.py` | Duplicated `GitHubGraphqlClient`, `_iso`, env var parsing. |
| 53 | `settings.py:65-78,413-426` | Redundant nested `os.environ.get("X", os.environ.get("X", ...))` — same key twice. |
| 54 | `branch_planner.py:29` | No deduplication when LLM rewrite matches original query. |
| 55 | `model.py:42-45` | `bind_tools` creates new chain, `self.runnable`/`self.fallbacks` never used. |
| 56 | `search/__init__.py:45-46` | `_circuit_breaker` and `_search_single_provider` in `__all__` with underscore prefix. |
| 57 | `provider_options.py:16` | `arguments: dict` mutable in frozen dataclass. |
| 58 | `search/searxng.py:90,246` | Timeout of `0.0` from env means infinite timeout in httpx. |
| 59 | `search/google_cse.py:57-62` | Only first domain filter used — rest silently ignored. |
| 60 | `content/summary.py:111` | `int(os.environ.get(...))` without try/except — crashes on non-integer. |
| 61 | `search/reddit.py:54` | 2s sleep added per-call — doesn't prevent rate-limiting for concurrent requests. |
| 62 | `content/windowing.py:28` | `_find_boundary_index` always cuts near end — produces uneven chunks. |
| 63 | `content/fetch_pipeline.py:168-175` | Telemetry span ends before specialized resolution check — span misses success. |
| 64 | `content/fetch_pipeline.py:388-407` | Duplicated Jina reader fallback pattern (appears twice). |
| 65 | `content/fetch_pipeline.py:88-90` | `_maybe_specialized` swallows parser exceptions silently. |
| 66 | `scrape/universal_html.py:89-90` | Duplicate env var name `"BROWSER_EXECUTABLE_PATH"` in lookup list. |
| 67 | `scrape/universal_html.py:611-612` | Duplicate env var assignment (copy-paste). |
| 68 | `ab_testing/assignment.py:54-59` | `Assignment.shadow_mode` never set — shadow mode plumbing disconnected. |
| 69 | `ab_testing/wiring.py:73` | `settings.ab_shadow_mode_default` is never consulted — dead setting. |
| 70 | `rerank/models.py:73-77` | `RerankEmbeddingContext.find` is O(n) linear scan — build dict in init. |
