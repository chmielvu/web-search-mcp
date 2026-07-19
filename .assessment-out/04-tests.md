# 04 — Test Coverage & Quality Audit (web-search-mcp)

> Evidence-based. PowerShell, 2026-07-15.

## 1. Test inventory

`pytest --collect-only -q`: `767 tests collected, 4 errors in 17.97s`.

128 Python files under `tests/` (`conftest.py` + 2 fixtures in `tests/fixtures/` + 4 in `tests/cli/`). 124 of 128 `test_*.py` files collect; **4 fail at import**:

- `tests/test_diversity_ranking.py` — imports removed `kindly_web_search_mcp_server.rerank.diversity.select_diverse_slate`
- `tests/test_rerank_core.py` — imports removed `diversity_stage.DiversityStageOutcome`
- `tests/test_rerank_pipeline_eval.py` — `from rerank_eval_diversity import tune_diversity` (wrong namespace)
- `tests/test_public_output_serialization.py` — collection error

### Top 10 by test count

`test_ab_integration.py` 42 · `test_youtube.py` 39 · `test_ab_models.py` 32 · `test_server.py` 25 · `test_gemini_search_tool.py` 19 · `test_provider_health_and_content_quality.py` 18 · `test_brightdata_common.py` 18 · `test_youtube_api_enrichment.py` 17 · `test_ddg_unit.py` 14 · `test_intent_policy.py` 13.

## 2. Coverage by top-level package

| Package (src files) | Coverage | Test count |
|---|---|---|
| `search/` (~60) | ✅ Heavy | 25+ files. Orchestrator, branch executor/planner, router, provider plan/config/health, intent policy, search service/contracts/quality; per-provider unit tests for searxng, tavily, ddg, degoog, brave, brightdata×3, langsearch, reddit, github×3, arxiv, hackernews, wikipedia, telegram×2, composio. |
| `content/` (29) | ✅ Solid | 9 files. `page_content_resolver`, `content_{status_classifier,windowing,observability}`, sitemap orchestrator+generate, whisper, telethon, jina_reader, firecrawl_stage; YouTube content via `test_youtube.py`. |
| `cli/` (35) | ⚠️ Partial | 4 in `tests/cli/` + `cli_search_service`, `analytics_*`, `remote_clients`, `composio_client`. **8 of 24 commands untested** (§4); 11/18 existing CLI tests fail. |
| `cache/` (8) | ✅ Solid | 6 files. `exact_lru_cache`, `page_cache_duckdb`, `query_cache_provider_key`, `qdrant_search`, `transcript_cache`, `cache_observability`. |
| `rerank/` (17) | ✅ Heavy (1 broken) | 18 files. 12 `test_rerank_*` + voyage/jina/bm25 + entity rerank. `test_diversity_ranking.py` broken. |
| `analytics/` (43) | ✅ Heavy | 12+ files. 5× `test_analytics_*`, `duckdb_analytics`, `quality_metrics_compute`, `pipeline_tables`, `ai_search_provider_tracing`, `eval_*`, `judge_*`, `grafana_dashboard_json`. |
| `tools/` (14) | ✅ Solid | `test_server.py` (25), `test_tool_{descriptions,profiles,search_transform}`, `test_gemini_search_tool` (19), `test_composio_tools`. `test_public_output_serialization.py` broken. |
| `observability/` (~19) | ✅ Adequate | `observability_{events,logging}`, `middleware_observability`, `agent_steering_middleware`, `phoenix_tracing`, `ai_search_provider_tracing`. |
| `classifier_service/` (`src/classifier_service/`, 2 files) | ❌ **Zero tests** | `tests/test_gliner_client.py` covers the lazy client only; the standalone FastAPI service (`server.py`, `runtime.py`) is **untested**. |

## 3. Mock strategy

CLAUDE.md mandates `kindly_web_search_mcp_server.*`. **Compliant in the vast majority** (e.g. `tests/test_brightdata_common.py:84`, `tests/test_degoog.py:41`, `tests/test_ab_integration.py:558`).

**Out-of-namespace exceptions:**

- `tests/test_ddg_unit.py:101, 121, 138, 155, 178, 187, 197` — `patch("ddgs.DDGS", …)`. Works only because `from ddgs import DDGS` is a function-local import at `src/kindly_web_search_mcp_server/search/providers/ddg.py:72`. If the source ever moves to a top-level import, **all 7 tests silently no-op**.
- `tests/test_rerank_pipeline_eval.py:162` — `patch("rerank_eval_capture.rerank_results")` (file also collection-broken).
- `tests/test_server.py:654` — `patch("importlib.import_module")` (stdlib, one-off, acceptable).

**No test makes an unauthenticated live network call** without an opt-in guard. Only `tests/test_live_fetch_urls.py:19` hits the real network and is gated on `RUN_LIVE_TESTS=1` + `BROWSER_EXECUTABLE_PATH`. All other provider tests use `httpx.MockTransport` or `AsyncMock` (`test_brave_providers.py:26`, `test_degoog.py:104`, `test_searxng_unit.py:64`).

## 4. CLI command coverage (24 commands)

Verified via `@*_app.command("…")` in `src/kindly_web_search_mcp_server/cli/commands/*.py`. **Untested (8):**

- `search academic` (`cli/commands/search.py:125`)
- `sitemap generate` (`cli/commands/sitemap.py:17`) — module tested, Typer wrapper is not
- 6× `experiments *`: `list` (`:57`), `enable` (`:88`), `disable` (`:147`), `conclude` (`:206`), `stats` (`:282`), `create` (`:324`; only the non-interactive guard is tested, not the happy path)

11 of 18 existing CLI tests **fail in the current run**. Example: `tests/cli/test_native_cli_phase3.py::test_links_discover_emits_json_payload` patches `kindly_web_search_mcp_server.cli.commands.content.fetch_content_payload`, which doesn't exist there (real function is in `cli.services.*`).

## 5. Flakiness signals

- Real `time.sleep(0.15)` ×3 in `tests/test_duckdb_analytics.py:116, 152, 197`; `time.sleep(1.1)` in `test_page_cache_duckdb.py:65` and `test_transcript_cache.py:89`.
- `asyncio.sleep`: 60 s in `tests/test_qdrant_search.py:34`, 2 s in `test_hard_budget_timing.py:68`, 10 s ×2 in `test_retrieval_budget.py:66, 139`, 5 s ×3 in `test_shared_embedding_cancellation.py:54, 116, 286`, 10 s in `test_async_helpers.py:46`.
- **No** `pytest.mark.flaky`, `pytest.mark.timeout`, or `asyncio.wait_for` to bound tests. Time-based tests can hide behind CI latency.
- `pytest-of-Jan` permission errors observed (environmental, not test code).

## 6. Test quality (3 representative files)

**`tests/test_brightdata_common.py` (18 tests)** — strong. Four unit-test classes, one assertion per test. URL builder flags, error detection on 407/502, malformed-item skipping, valid/invalid JSON-extra. All patches under correct namespace. Edge cases (empty title, non-dict, custom country/lang) covered. **Highest-quality file in the suite.**

**`tests/test_youtube.py` (39 tests)** — broad, shallow. Imports from 5 sub-modules, single-observable assertions. Misses parametrization: URL-format cases would shrink to one parametrized test. `TestTranscriptCascade` (6 tests) failed in current run — likely `yt_dlp_backend._parse_json3` import path drift.

**`tests/cli/test_native_cli_phase3.py` (≥9 tests, mostly failing)** — right pattern, wrong targets. `CliRunner` + service patches + JSON envelope assertions (`payload["meta"]["command"]`, `payload["data"][…]`). **But** patches target attributes that don't exist on `cli.commands.*`; 8/9 fail with `AttributeError`. Stale attribute paths are a recurring pattern.

## 7. Missing critical tests (top 5)

1. **Error-envelope shape on provider failure** — `format_tool_error`/`StructuredToolError` (`src/kindly_web_search_mcp_server/errors.py:23, 277`) is exercised only in `tests/test_middleware_observability.py:89`. No test asserts the JSON shape (retry-after, classification, action) when SearXNG returns 502 or Tavily returns 429.
2. **RRF merge edge cases** — `tests/test_merge.py:51` covers the 2-list happy path. No test for `reciprocal_rank_fusion([])`, `reciprocal_rank_fusion([single_list])`, or `reciprocal_rank_fusion([list_a, list_a])`.
3. **Prompt-injection in `--research-goal`** — `research_goal` is interpolated at `src/kindly_web_search_mcp_server/prompts/provider_gemini.py:144` (no escape) and `prompts/rerank.py:7`. No test injects `</USER_QUERY><SYSTEM>…</SYSTEM>` to confirm the cross-encoder prompt is safely wrapped.
4. **DuckDB lock contention** — only `tests/test_duckdb_analytics.py` is DuckDB-heavy. The `IO Error: Cannot open file … being used by another process` observed in §9 is **not asserted**. `src/kindly_web_search_mcp_server/utils/duckdb_log_handler.py:61` (`_connect`) has no concurrent-writer test.
5. **Content-fetch timeout propagation** — `tests/test_server.py:531` patches `TimeoutError`; nothing verifies `TOOL_TOTAL_TIMEOUT_SECONDS` surfaces a classified `TimeoutError` envelope to the caller (not a generic `httpx.ReadError`).

## 8. Test infrastructure

`tests/conftest.py` is 15 lines with **1** fixture: autouse session-scoped `patch_settings` injecting dummy `SEARXNG_BASE_URL`/`TAVILY_API_KEY`. **Risk:** the dummy `https://searx.example.org` silently masks any new code that forgets to opt out. Only 2 fixtures in `tests/fixtures/`; data construction is mostly inline. 30+ `@pytest.fixture` decorators scattered across files; no `tests/{cli,rerank}/conftest.py`. `httpx.MockTransport` is re-defined in 4+ files (`test_brave_providers.py:26`, `test_degoog.py:104`, `test_langsearch_provider.py:83`, `test_remote_clients.py:29`) — minor duplication, no `tests/factories/`.

## 9. Test suite run

```
python -m pytest -q --tb=no --basetemp=.tmppytest \
  --ignore=tests/test_{diversity_ranking,public_output_serialization,rerank_core,rerank_pipeline_eval,
                       qdrant_search,hard_budget_timing,retrieval_budget,shared_embedding_cancellation,
                       hf_inference_embeddings,page_cache_duckdb,duckdb_analytics}.py
```

**Result: `97 failed, 623 passed, 1 skipped in 433.77s (0:07:13)`** — plus 4 collection errors and 28 environment errors (`IO Error: Cannot open file … duckdb_data\logs\process_logs.duckdb … being used by another process` from `utils/duckdb_log_handler.py:61`). **Without `--ignore` flags the suite is unrunnable** in this sandbox. The 97 failures cluster in `tests/cli/test_native_cli_{phase2,phase3,scaffold}.py`, `tests/test_analytics_*`, `tests/test_ab_views.py`, `tests/test_youtube.py` (TestTranscriptCascade), `tests/test_branch_executor.py`, `tests/test_firecrawl_stage.py`, `tests/test_batch_orchestrator.py`. Most are stale attribute paths; a few are real regressions hidden behind them.

## Bottom line

767 tests across 124 files with strong `search/`, `rerank/`, and `analytics/` coverage, but 4 collection-broken files, 8 of 24 CLI commands untested, the standalone `classifier_service` server completely untested, and 5 critical scenarios (error envelope, RRF edges, prompt-injection in `research_goal`, DuckDB lock contention, content-fetch timeout classification) with no direct tests.

---

**TESTS REVIEW DONE — 767 collectable tests across 124 files cover `search/`, `rerank/`, and `analytics/` well, but 4 files are collection-broken, 8 of 24 CLI commands are untested, the `classifier_service` has zero tests, and 5 high-risk scenarios lack direct tests.**
