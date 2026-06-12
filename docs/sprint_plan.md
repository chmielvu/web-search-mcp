# Sprint Plan — Fix Critical Bugs from code_review.md

## Sprint Goal
Fix all 11 Critical bugs and 12 High-Priority bugs from the 2026-06-11 code review.

## Week 1: Critical Bugs (11 issues)

| Day | Issue | Location | Fix |
|-----|-------|----------|-----|
| 1 | #1 Semaphore useless | `search/query_execution.py:144` | Make semaphore module-level or env-configurable per intent |
| 1 | #2 Gemini judge wrong key | `server.py:1463` | Change `c.get("uri","")` to `c.get("url","")` or normalize key |
| 2 | #3 Truthiness drops zero | `errors.py:56-59` | Replace `if self.status_code:` with `if self.status_code is not None` |
| 2 | #4 Double-count telemetry | `agent/mcp.py:63` | Move success=True record to after operation succeeds |
| 3 | #5 Missing getattr guard | `agent/mcp.py:72` | Add `getattr(ctx, "session_id", None)` fallback |
| 3 | #6 Unstable session ID | `middleware/session_tracking.py:28` | Use UUID or counter instead of `id()` |
| 4 | #7 Cache mutation on lookup | `server.py:2008` | Copy dict before mutating: `dict(exact_cached)` |
| 4 | #8 AttributeError on None | `server.py:556` | Add `settings.gemini_api_key and settings.gemini_api_key.strip()` |
| 5 | #9 None session_state | `search/pipeline.py:395` | Guard: `if state: state.last_intent = ...` |
| 5 | #10 No upsert in page cache | `cache/page_duckdb.py` | Add `INSERT OR REPLACE` or dedup on write |
| 6 | #11 Unbounded pagination | `content/stackexchange.py:246` | Add `max_pages` parameter with default 10 |

## Week 2: High-Priority Bugs (12 issues)

| Day | Issue | Location | Fix |
|-----|-------|----------|-----|
| 6 | #12 Hardcoded SDK version | `telemetry.py:508` | Read from package metadata or env |
| 7 | #13 Settings singleton mismatch | `telemetry.py:543` | Use existing singleton instead of new instance |
| 7 | #14 HTTP timeout too tight | `search/pipeline.py:163` | Increase or make configurable per provider |
| 8 | #15 Query not URL-encoded | `search/jina.py:90` | Use `urllib.parse.quote` |
| 8 | #16 New client per request | `search/pollinations.py:107` | Use shared pipeline client |
| 9 | #17 Sync DNS blocks loop | `content/safe_fetch.py:130` | Use `asyncio.getaddrinfo` or aiohttp resolver |
| 9 | #18 Path traversal | `agent/runner.py:90` | Validate path is within allowed directory |
| 10 | #19 Module-level AsyncClient | `rerank/jina.py:17` | Create client per event loop or use factory |
| 10 | #20 Double-await gather | `search/query_execution.py:126` | Remove unreachable else branch |
| 11 | #22 No timeout on fetch | `agent/content_tools.py:77` | Add `asyncio.wait_for` wrapper |
| 11 | #23 Shadow mode wrong capture | `search/pipeline.py:322` | Capture pre-rerank list for shadow comparison |
| 12 | #21 (duplicate of #1) | — | Already fixed |

## Verification
- Run full test suite: `pytest`
- Run focused slice: `python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_search_orchestrator.py tests/test_search_router.py`
- Check lint: `ruff check src/`

## Notes
- Do NOT touch dead code or quick wins in this sprint — separate cleanup sprint later.
- Each fix must include a test if one does not exist.
- Update CHANGELOG.md after each batch.
