
## Code review safe-refactors — 2026-07-20

- 2026-07-20 [DECISIONS] Applied code-review items #2, #4, #5, #6, #9, #10 in a single safe pass. Skipped #3, #7, #8 because origin `e07ca83` (`simplify: consolidate _get_int_env/_get_float_env, summary modes, and clean up hot-path imports`) already covered them — `utils/environment.py` now centralizes the 4 `_get_int_env` copies and the `anchor_today` lazy import in `content/summary_backend.py` is gone. Items #11+ (settings bypass), #12 (DuckDB connect lifecycle), #13 (LLM router client caching), #14 (deepcopy on cache hit), #15 (GitHub Issues↔Discussions helpers), #16 (DuckDB connect pattern), #17 (`model_copy(update=...)` helper), #18 (duplicate `normalize_query` in planning) are explicitly deferred — user excluded them.
- 2026-07-20 [DECISIONS] `#4` partial-apply: the *first* `dc.merged_candidates = [result.model_copy() for result in merged]` was redundant and replaced with `list(merged)`. The *second* `.model_copy()` at the rerank call site was kept because `apply_entity_overlap_boost` writes `candidate.score` directly on the model instance, which would otherwise leak into the analytics snapshot. The decision is pinned by `tests/test_search_ranking.py::test_dc_merged_candidates_unchanged_when_rerank_mutates_inputs` (asserts rerank-side list mutation does not corrupt the snapshot).
- 2026-07-20 [CODE] `#2` removed dead `if TYPE_CHECKING: pass` and the unused `TYPE_CHECKING` import from `analytics/writers/connection.py` and `analytics/writers/schema.py`.
- 2026-07-20 [CODE] `#5` added `_memoize_canonicalize` helper in `search/merge.py`; `reciprocal_rank_fusion` takes an optional `canonicalize: Callable[[str], str]` kwarg. `merge_search_results` and `rank_and_finalize` share one memoizing wrapper per call so each distinct raw URL is canonicalized at most once across the overlap counter, both RRF invocations, and the per-result `url_key` lookup. Caps each distinct raw URL to one canonicalize per `rank_and_finalize` call (was up to 5×).
- 2026-07-20 [CODE] `#6` hoisted `len(markdown.split())` into a `word_count` local in `_fetch_via_jina`, `_fetch_via_crawl4ai`, and `_fetch_via_camoufox` in `content/stages.py`. `_fetch_via_local` left alone (its two `markdown.split()` calls live in mutually exclusive PDF vs HTML branches).
- 2026-07-20 [CODE] `#9` narrowed bare `except Exception: pass` in `cache/page_duckdb.py::_ensure_schema` to `except duckdb.Error` + `logger.warning("page_cache: skipped url_hash index (non-fatal): %s", exc)`. Added module-level `logger`. Non-duckdb exceptions now propagate.
- 2026-07-20 [CODE] `#10` extracted `use_llm_why` boolean + `_why_for(role, llm_label)` helper + `_DETERMINISTIC_WHY` dict in `search/planning.py`. The 6× duplicated `QueryBranch(why=...)` conditional collapsed to a single selector used by all 5 paid/neural/specialized branches.
- 2026-07-20 [TOOL] 4 new test files: `tests/test_search_ranking.py` (2 tests), `tests/test_search_merge_cache.py` (3 tests), `tests/test_page_duckdb_schema_errors.py` (2 tests), `tests/test_search_planning_why.py` (4 tests). All 11 new tests pass alongside the 12 pre-existing tests in `test_merge.py` / `test_stages.py` / `test_page_cache_duckdb.py` / `test_query_embedding_propagation.py` that exercise the touched code. 23/23 focused tests pass; ruff clean on all 7 production files + 4 new test files.
- 2026-07-20 [DISCOVERIES] 3 pre-existing test failures in `test_rerank_pipeline_integration.py` (signature drift on `score_candidates` and `top_k`) — confirmed unchanged from clean `HEAD` `e07ca83` via stash-test, not regressions from this work.

## quick_web_search → Parallel AI refactor — 2026-07-19T04:50Z

- [CODE] `search_queries: list[str]` (1-5, 2-3 recommended) + `objective: str` (both required). Removed always-None `answer` field; response field renamed `query` → `search_queries`.
- [CODE] CLI: repeatable `--search-query` (1-5) + required `--objective`; reference_data lists both.
- [CODE] New `quick_web_search.py` module — self-contained with models, impl validation (nonempty, ≤5, nonblank), MCP registration, `async with` lifecycle, `max_age_seconds >= 600`.
- [CODE] Removed Composio/Tavily implementation from `composio_tools.py`; deleted `QuickWebSearchCitation`, `QuickWebSearchResponse`, `QuickWebSearchResultType` from `models.py`.
- [CODE] Added `parallel-web>=1.0` dep + `PARALLEL_API_KEY` to settings; `uv.lock` contains `parallel-web==1.1.0` (sync interrupted by locked .exe, but package installed).
- [CODE] Full Parallel inputs exposed: `search_queries`, `objective`, `max_results`, `max_chars_total`, `max_chars_per_result`, `client_model`, `session_id`, `include_domains`, `exclude_domains`, `after_date`, `location`, `max_age_seconds` (min 600), `timeout_seconds`, `disable_cache_fallback`.
- [CODE] Full Parallel outputs: `search_id`, `session_id`, `warnings`, `usage` + per-citation `publish_date` and `excerpts`.
- [TOOL] Unit tests: happy path, multi-query kwargs, empty results, API error, missing key, warnings/usage, plus 4 validation tests (empty list, >5, blank member, max_age<600).
- [TOOL] CLI tests: 2-query success with list await assertion, missing objective, missing search-query.
- [TOOL] Ruff clean on our changed files; full repo has 29 pre-existing unrelated findings.
- [CODE] Updated README, CHANGELOG, workflow.py, prompts.py, CLI AGENTS.md, reference_data.py.
- [TOOL] GitNexus impact: `_quick_web_search_impl` (LOW, 2 callers), `fetch_quick_web_search_payload` (LOW, 1 caller) — both accounted for.

## [DISCOVERIES] Parallel Search wire contract (observed 2026-07-19)
- Request: `POST /v1/search` with `search_queries: list[str]` + `objective: str`. When `mode`/`advanced_settings` omitted, SDK sends only `search_queries`+`objective` (no `mode` key); when set, `mode="advanced"` is present. `answer` is never sent (Parallel doesn't synthesize).
- Response `SearchResult` fields: `search_id`, `session_id`, `results[]` of `{url, title, publish_date (nullable), excerpts: list[str] markdown}`, `warnings[]` of `{message, type, detail}`, `usage[]` of `{name, count}`.
- Live behavior (key from `.env`, not `os.environ`): latency ~2.3-4.5s in advanced mode; default `max_results`=10; `location="us"` accepted silently; usage returned as `[{name: "sku_search", count: 1}]`. No contract drift vs our `QuickWebSearchResponse`.
- `settings.parallel_api_key` reads `.env` via `load_dotenv` in `settings.py` (lines 8-20); raw `os.environ.get` misses it. Check via the settings object, not `os.environ`.
- `AsyncParallel` supports `async with` (confirmed `__aenter__`/`__aexit__`). Pass a mock `httpx.AsyncClient(transport=MockTransport(...))` via `httpx_client=` to verify wire contract without quota (`tests/test_quick_web_search.py::TestLiveShapeContract`).

## Assessment Cross-Evaluation Remediation — 2026-07-19

- 2026-07-19T02:15Z [CODE] Completed 10-step remediation plan from `local://mcp-assessment-actionable-recommendations-plan.md`. 36 assessment artifacts cross-evaluated against source; 10/11 findings CONFIRMED, 1 PARTIALLY CONFIRMED/REFUTED (firecrawl already in pyproject.toml).
- 2026-07-19T02:15Z [TOOL] **Step 1**: OTel Phoenix stdout→stderr redirect via `_redirect_stdout_to_stderr()` combining Python-level + fd-level (`os.dup2`) redirect. Verified: `uv run web-search-cli doctor` emits clean JSON stdout.
- 2026-07-19T02:15Z [CODE] **Step 2**: Crawl4AI dict serialization fix — `fetch_markdown` extracts `data.get("markdown")` instead of `str(dict)`.
- 2026-07-19T02:15Z [CODE] **Step 3**: `latency-breakdown` SQL subquery wrap for DuckDB ORDER BY binder. Verified with live DuckDB.
- 2026-07-19T02:15Z [TOOL] **Step 4**: `uv sync` confirmed `firecrawl-py` installed; added `ImportError` guard + doctor check.
- 2026-07-19T02:15Z [DECISIONS] **Step 5**: User directive: MCP-only removal. Deleted `analytics/tools.py`; removed from `TOOL_COVERAGE` and SKILL.md MCP column. CLI `analytics query`/`analytics report` commands, `analytics://` MCP resources, and `analytics/__init__.py` re-exports preserved.
- 2026-07-19T02:15Z [CODE] **Step 6**: SKILL.md: `provider_health`→`provider-performance`; `diagnostic` profile zeroed; `--num-results` removed from `search web`.
- 2026-07-19T02:15Z [DECISIONS] **Step 7 (DEFERRED)**: Telemetry star-import cleanup deferred. Full explicit re-export of 150+ `__all__` names proved fragile — each submodule change would break `__init__.py`. Public sub-modules retain `from .module import *` with `ruff: noqa: F401, F403, F405`. Internal imports (`_internal`, `constants`, `init`) use explicit named imports. No regressions; ruff clean.
- 2026-07-19T02:15Z [CODE] **Step 8**: `canonicalize_url` implementation moved to `utils/url_canonicalize.py`; 6 content/ + 3 external files updated; `search/normalize.py` re-exports. Verified: zero `from.*search.normalize` in content/.
- 2026-07-19T02:15Z [CODE] **Step 9**: Deleted `test_diversity_ranking.py` and `test_rerank_pipeline_eval.py`. Fixed `test_rerank_core.py` import, `test_public_output_serialization.py` syntax. Updated 6 CLI test patches to `cli.services.*`. Fixed `test_experiments_create_requires_config` for stderr OTel banner. Updated catalog count (11) and help-text assertions. 772 tests collect, 31 focused pass.
- 2026-07-19T02:15Z [CODE] **Step 10**: Updated `rerank/AGENTS.md` (removed stale files, added current), created `telemetry/AGENTS.md`. CHANGELOG entry added.
- 2026-07-19T02:15Z [DISCOVERIES] `scripts/rerank_eval_diversity.py` still imports deleted `rerank.diversity` — documented as stale in guide, requires migration.

## Content Fetch Reliability + Efficiency — 2026-07-18

- 2026-07-18T10:23Z [USER] Approved `local://content-tools-reliability-plan.md` (13 steps, 4 phases).
- 2026-07-18T10:30Z [CODE] Implemented page-cache pre-check inside `run_batch_fetch` (`content/batch_orchestrator.py`) so batch fetches reuse cached pages; per-URL exceptions are now isolated and return `status="error"`, `fetch_backend="exception"` instead of failing the whole batch.
- 2026-07-18T10:35Z [CODE] Made `PageDuckDBCache`/`PageCache` async via `alookup`/`astore` (thread-pool) while keeping sync `lookup`/`store` for tests/CLI; added resilient fallback so cache errors never fail the tool. Updated `tools/content.py` and `cli/services/content.py` callers to `await` the async cache surface.
- 2026-07-18T10:40Z [CODE] Added per-stage retry with exponential backoff inside `content/stages.py` for Jina Reader, local HTTP, and Crawl4AI; Crawl4AI retry respects `retryable=False`. No stage-level retry for Camoufox (its internal retry is expanded separately).
- 2026-07-18T10:45Z [CODE] Added per-stage timeout budgets in `content/fetch_pipeline.py` (Jina 25s, Crawl4AI 30s, local 20s, Camoufox 35s) capped by remaining total budget; wired into Jina Reader and local HTTP.
- 2026-07-18T10:50Z [CODE] Added module-level Jina Reader circuit breaker (`content/jina_reader.py`) that opens after 3 failures in 60s and falls through to Crawl4AI/local.
- 2026-07-18T10:55Z [CODE] Strengthened Camoufox internal retry from 1 attempt to 3 with exponential backoff (2s, 4s, 8s) and updated `tests/test_remote_clients.py`.
- 2026-07-18T11:00Z [CODE] Raised `classify_markdown` success word threshold from 30 to 80 and added SPA shell detection (`content/status_classifier.py`); added tests for SPA shell and short content.
- 2026-07-18T11:05Z [CODE] Added content-type validation in `content/safe_fetch.py` to reject non-HTML/XML/plain responses; allowed missing content-type to avoid breaking misconfigured servers.
- 2026-07-18T11:10Z [CODE] Added `content_quality` and `content_word_count` to `GetContentResponse` and `BatchContentResult` (`models.py`) and populated them in `tools/content.py`, `cli/services/content.py`, and `content/batch_orchestrator.py`.
- 2026-07-18T11:15Z [CODE] Added boilerplate stripping in `content/sanitize.py` and applied it in `content/extract.py`; improved regex fallback for `<a>`, `<code>`, `<pre>`, `<blockquote>`, and `<img>`.
- 2026-07-18T11:20Z [TOOL] Targeted tests: `test_page_cache_duckdb.py`, `test_batch_orchestrator.py`, `test_content_status_classifier.py`, `test_stages.py`, `test_remote_clients.py` all pass (28 total). Ruff/lint clean.
- 2026-07-18T11:25Z [CODE] Jina Reader optimization: switched base headers to `X-Respond-With: frontmatter`, `X-Preset: research`, `X-Retain-Links: none`, `X-Retain-Images: none`, `X-No-Cache: true` (`content/jina_reader.py`). Removed the tentative `jina_preset` FetchOptions field.
- 2026-07-18T11:30Z [CODE] Fixed cache-hit backend indicator to `cache` in `tools/content.py`, `cli/services/content.py`, and `content/batch_orchestrator.py`; added cache store to `cli/services/content_batch.py` for fully-windowed batch results.
- 2026-07-18T11:35Z [TOOL] Live production verification (real websites): `get_content https://docs.python.org/3/tutorial/` returned `status=success`, `fetch_backend=jina_reader`, `content_word_count=397`, clean frontmatter output. Second call returned `fetch_backend=cache`. Batch with two Python docs URLs succeeded; second batch returned both results with `fetch_backend=cache`. Batch fetch uses windowed parallelism (`asyncio.gather` per window with semaphore up to `max_concurrency`).
- 2026-07-18T11:20Z [DECISIONS] Kept `classify_quality` sigmoid inflection at 30 words; only the hard `classify_markdown` floor moved to 80 words. Kept Camoufox stage-level retry disabled to avoid double retries with the expanded internal client retry.

## Search Package Restructure + LangSearch Integration — 2026-07-17

- 2026-07-17 [USER] Approved `local://search-restructure-plan.md` (11 steps, 6 phases): dead code cleanup, schema consolidation, provider restructure into `search/providers/`, Bing sidecar cleanup, LangSearch Web Search API integration, final verification.

## FastMCP v3 Migration + Stdio Cold-Start Fix — 2026-07-17

- 2026-07-17T23:00Z [CODE] FastMCP v3 migration: replaced monkeypatched resource/prompt list/read with native v3 decorators, `ProvidersAsTools`/`ResourcesAsTools` transforms, `analytics_app` via `providers=[]`. Rewrote analytics dashboard queries, UI, and reports against verified live DuckDB schema.
- 2026-07-17T23:00Z [CODE] Fixed `apply_tool_profile` hiding resources/prompts: `enable(only=True, components={"tool"})` adds a blanket `Visibility(False, match_all=True)` that disables all component types. Added `mcp.enable(components={"resource", "template", "prompt"})` after the tool allowlist. Confirmed via FastMCP source (`base.py:enable`).
- 2026-07-17T23:00Z [CODE] Fixed cold-start stdio timeout: lazy imports of `openai.resources.chat` (via `llm/router.py`), `nltk`, and `scipy` (via `search/keyword_extract.py`'s `rake_nltk`) contended for the Python global import lock during first tool call, blocking the anyio event loop. Pre-imported `openai.resources.chat` in `router.py`, moved `rake_nltk` import to module level in `keyword_extract.py`, added `_warm_heavy_imports()` helper in `server.py` called from `main()` before `mcp.run()`.
- 2026-07-17T23:00Z [TOOL] Verified: 25/25 tests, production stdio subprocess returns 15 results from 7 providers in ~23s tool time, all 7 resources and 2 prompts visible via live MCP. Connected MCP process needs reload to pick up new code.

- 2026-07-17 [CODE] **Step 1 — Dead code**: Deleted `search/errors.py`; removed broken `from ..retry import retry_with_backoff` imports and replaced `retry_with_backoff(lambda: asyncio.to_thread(_sync_search), ...)` with direct `await asyncio.to_thread(_sync_search)` in `academic_arxiv.py`, `academic_crossref.py`, `academic_openalex.py`.
- 2026-07-17 [CODE] **Steps 2-4 — Schema consolidation**: Rewrote `provider_catalog.py` with `ProviderDefinition` schema (removed `group: ProviderGroup`, added `adapter_module: str` + `adapter_function: str`); updated `_definition()` factory and all 21 call sites with `providers.<module>` prefixes; removed `ProviderGroup` enum from `contracts.py`; deleted `_ADAPTER_PATHS` from `provider_registry.py`; derived `PROVIDER_ADAPTERS` from `PROVIDER_DEFINITIONS_LIST`; simplified `select_provider_names` to return all reachable providers (group filtering removed).
- 2026-07-17 [CODE] **Steps 5-7 — Provider restructure**: Created `search/providers/__init__.py`; moved 24 provider files to `search/providers/` (`base_provider.py`→`base.py`); moved 7 academic files to `search/academic/`; fixed relative imports (`from ..`→`from ...`, `from .base_provider`→`from .base`, `from .normalize`→`from ..normalize`); updated all external callers across `src/` (cli/services/ai.py, content/resolvers/telegram.py, tools/ai_search.py, youtube/, planning.py) and 17 test files.
- 2026-07-17 [CODE] **Step 8 — Bing cleanup**: Deleted `collect_bing_sidecar`/`search_bing_sidecar` from `brightdata_common.py`; moved Bing search into `brightdata.py` as private `_search_bing`; deleted `brightdata_bing_join_grace_seconds` setting; updated test mocks; `brd_json=1` preserved in URL builders.
- 2026-07-17 [CODE] **Step 9 — LangSearch API**: Verified API contract via docs at `https://docs.langsearch.com/api/web-search-api` (POST `https://api.langsearch.com/v1/web-search`, Bearer auth, response under `data.webPages.value` with `name`→title, `url`→link, `snippet`→snippet); added settings (`langsearch_api_key`, `langsearch_timeout_seconds`, `langsearch_base_url`); created `search/providers/langsearch.py` adapter; registered in `provider_catalog.py` after jina; added to `_NEURAL_CANDIDATES` in `planning.py`.
- 2026-07-17 [CODE] **Step 10 — Public surface**: Added `langsearch` to `providers_configured` in `tools/_helpers.py`; added LangSearch status line in `tools/status.py`; updated `test_provider_registry.py` to expect 22 providers.
- 2026-07-17 [CODE] **Step 11 — Behavior test**: Created `tests/test_langsearch_provider.py` (6 tests: config error on empty key, result parsing, count clamped to 10, HTTP error propagation via `raise_for_status`, empty query returns empty, catalog round-trip). All 6 pass.
- 2026-07-17 [TOOL] Verification: 7 structural grep checks pass (dead module gone, broken imports fixed, no `group` attr in catalog, `ProviderGroup` removed, `_search_bing` exists / `search_bing_sidecar` gone / `brightdata_bing_join_grace_seconds` gone, `brd_json=1` preserved, 22 adapter entries total). Ruff clean on all 12 refactoring-touched files; 30 pre-existing errors in untouched files confirmed via `git show HEAD:`. Broad smoke test across 19 restructured-provider test files: 94 passed, 10 failed in 14.49s — all 10 failures pre-existing on clean HEAD 7787097 (stash-verified), zero regressions. 6 LangSearch tests pass.
- 2026-07-17 [DISCOVERIES] Import fix order matters: `from ..`→`from ...` must run BEFORE specific `from .normalize`→`from ..normalize` fixes, else the general rule over-replaces specific ones. Windows lacks `sed` on PATH — all bulk file ops via Python scripts.
- 2026-07-17 [DISCOVERIES] Pre-existing test failures confirmed via `git stash` on clean HEAD 7787097 (NOT regressions — identical failure messages before and after): `test_brightdata_outer_timeout_covers_run_provider_budget` (assert 10.0 >= 63.0), `test_google_url_news` (tbm=nws missing), `test_resolve_payload_base_default` (KeyError 'data_format'), `test_yandex_url_includes_region_and_brd_json` (brd_json=1 removed intentionally 2026-07-14), `test_yandex_alias_calls_run_provider_with_name` (IndexError), `test_search_merges_google_and_bing` (assertion mismatch), `test_search_returns_google_only_when_bing_disabled` (use_bing kwarg removed), `test_ddg_registered_as_free_peer_provider` (ModuleNotFoundError provider_config), `test_qdrant_embedding_timeout_cancels_inflight_task` (AttributeError _EMBEDDER), `test_search_qdrant_uses_hf_auth_token` (AttributeError _EMBEDDER).
- 2026-07-17 [DECISIONS] `select_provider_names` simplification: removed `specialized_names` filtering entirely — returns all reachable providers in catalog order; parameter kept for API compatibility but unused. LangSearch adapter handles both `data.webPages.value` and `webPages.value` response shapes defensively (docs confirmed `data.webPages.value`).
- 2026-07-17 [OUTCOMES] Search package restructure complete. Provider count: 22 (was 21, added langsearch). Files created: `search/providers/__init__.py`, `search/academic/__init__.py`, `search/providers/langsearch.py`, `tests/test_langsearch_provider.py`. Files deleted: `search/errors.py`. Files moved: 24 provider files to `search/providers/`, 7 academic files to `search/academic/`, `base_provider.py` renamed to `base.py`. All changes uncommitted on `main` branch (HEAD 7787097). Verification: 94 passed / 10 failed (all pre-existing, stash-verified) across 19 restructured-provider test files; 6/6 LangSearch tests pass; Ruff clean on all touched files.
- 2026-07-17 [TOOL] **Live verification**: Two CLI searches confirmed the restructure works in production: (1) "LangSearch Web Search API documentation" → 15 results from `['brave', 'ddg', 'langsearch']` with top result the verified docs page; (2) "FastAPI dependency injection tutorial" → 15 results from same providers. LangSearch successfully integrated and contributing alongside restructured providers. Pre-existing HuggingFace Windows warnings unrelated to this work.
- 2026-07-17 [BUGFIX] **Regression fix**: `brightdata_provider_call_timeout_seconds()` was incorrectly returning `search_retrieve_budget_seconds` (10s) instead of the proper calculation `max(google_timeout, bing_timeout) * 3 + 3` (63s). This caused BrightData to timeout when it previously worked in ~2s. Root cause: during schema consolidation, the function body was simplified incorrectly, losing the timeout math documented in 2026-07-14 entry.

## Rerank and JSONL Production Repair — 2026-07-16

- 2026-07-16T17:35:00+02:00 [USER] Approved the repair plan (100 -> 30 -> 15 monotone funnel, Nemotron-3-nano listwise RankLLM, no MMR/stack modes/diversity, clean three-stage analytics views, aiofiles runtime dependency/import fix, outcome JSONL result count).
- 2026-07-16T17:35:00+02:00 [CODE] Implemented the monotone funnel limits, settings parameters cleanup, signature overrides removal, `SearchResultWindow` deletion, cross-encoder and RankLLM stage limit truncations, `RerankStageSummary` telemetry collection, `vw_candidate_funnel` and `vw_rerank_timeline` views updates, `survived` field replacement in schema/observability, `aiofiles` dependency injection, and JSONL writer fixes.
- 2026-07-16T17:35:00+02:00 [TOOL] Verification checks:
  1. Python limits/settings check printed exactly `100 30 15 nvidia/nemotron-3-nano-30b-a3b:free`.
  2. CLI schema and help queries proved that `num_results` and `result_offset` are removed from user options and `result_window` from output schemas.
  3. Direct temporary JSONL write smoke test passed sequentially with correct `result_count`.
  4. Isolated 30-candidate RankLLM model probe completed in one single invocation (9.86s, 1995 input tokens, 1057 output tokens) returning complete bijective permutation.
  5. Live CLI search on `"RankLLM sliding window" full-list reranking` executed successfully (no bypass, 15 unique results returned, Gemini fallback, no warnings/errors).
  6. Recreated DuckDB query read-only and proved exact three canonical stages (`bi_encoder`, `cross_encoder`, `rankllm`), non-null statuses, n_0>=n_1>=n_2>=n_3 (66>=66>=30>=15), payload funnel counts, correct `vw_candidate_funnel` ranks, and `vw_rerank_timeline` chronological order.
  7. Ruff check on all touched files passed with zero findings; compileall check succeeded with no errors.

## Post-edit Production Reverification — 2026-07-16

- 2026-07-16T15:27:00+02:00 [USER] Required a fresh real-life `web-search-cli search web` run after subsequent edits and explicitly prohibited further pytest runs.
- 2026-07-16T15:27:00+02:00 [TOOL] Fresh production run `23df2bb5-d1b0-4d22-8c42-3388f01ea4ab` succeeded for `Cohere Rerank v4 calibration threshold guidance`: 61 unique candidates, Cohere full-pool 61/61, OpenRouter bounded at 20s, Gemini RankLLM accepted 30/30, conditional MMR reconstructed 30/30, final pool 61/61, and 15/15 unique final links persisted. Pipeline duration was 49.318s; shell wall time was 78.31s.
- 2026-07-16T15:27:00+02:00 [DISCOVERIES] Live output still emits LiteLLM debug noise and `Logging.async_success_handler was never awaited` during event-loop handoff. Search result, rerank telemetry, and persistence completed successfully despite the warning.

## Conditional RankLLM Live Verification — 2026-07-16

- 2026-07-16T15:05:00+02:00 [USER] Requested live end-to-end verification of the new reranking through the normal CLI pipeline.
- 2026-07-16T15:05:00+02:00 [CODE] Kept research-goal input cross-encoder-only while RankLLM receives the normalized query; fixed `rce` risk matching inside `primary-source`, native async RankLLM transport timeout, strict sliding-window permutation validation via `ast.literal_eval`, constructor stdout suppression, empty-provider overlap metadata, and the batched rerank-candidate column contract.
- 2026-07-16T15:05:00+02:00 [TOOL] Live CLI run `b348df7c-ab02-4809-9bf9-1cccc4d23be1` succeeded with 61 candidates and 15 unique persisted results in 52.784s pipeline time. Cohere reranked all 61 in 461.98ms; OpenRouter timed out at 20s; Gemini completed two RankLLM windows and was accepted for 30/30 candidates; conditional MMR triggered and reconstructed 30/30; final provider/model was `gemini` / `gemini-3.1-flash-lite`.
- 2026-07-16T15:05:00+02:00 [TOOL] Verification passed: Ruff on rerank, ranking, analytics candidate writer, and related tests; rerank-focused suite 79 passed plus 17 subtests.

## Bounded Retrieval Cancellation — 2026-07-15

- 2026-07-15T11:20:00+02:00 [CODE] Extracted the bounded cancellation drain pattern into `cancel_and_drain_tasks` in `utils/task_scope.py`. Refactored `TaskScope.wait_and_cancel` to call it.
- 2026-07-15T11:20:00+02:00 [CODE] Wired the new `cancel_and_drain_tasks` at the two main unbounded cleanup sites in `search/retrieval.py` (normal retrieve budget and caller cancellation paths) to ensure provider task unwinding is always bounded. Shared query embeddings remain shielded and uncancelled.
- 2026-07-15T11:20:00+02:00 [TOOL] Verification: added 3 deterministic async tests to `tests/test_shared_embedding_cancellation.py` covering resistant children, budget timeouts, and caller cancellation. Fixed mock adapter setup and query validation in existing tests. All 8 targeted tests and 26 server/service integration tests pass successfully.
- 2026-07-15T11:20:00+02:00 [TOOL] Smoke test: ran `web-search-cli` end-to-end search; all providers timed out gracefully under the budget constraint and returned a valid results/warnings payload with no hang.

## Live Web-Search Quality Campaign — 2026-07-15
- 2026-07-15T00:09:53+02:00 [CODE] Added modular `scripts/live_web_search_quality*.py` tooling and a fixed 50-query corpus. The actual FastMCP stdio runner enforces ten batches of five concurrent calls, exact-attempt/no-retry semantics, resumability, a first-batch DuckDB/debug gate, atomic manifests, JSONL exports, manual review, and pandas/pyarrow Parquet exports with verified row-count round trips.
- 2026-07-15T00:09:53+02:00 [TOOL] Completed one campaign at `test-results/live-web-search/20260714T213222Z`: 50 unique attempts, 10 completed batches, 40 successes, five MCP-wrapped timeouts classified as `timeout`, and five initial MCP-wrapped timeouts recorded as `transport_error` before the classifier fix. Successful calls all returned 15 structured results; latency p50 45.98s, p95 180.20s, max 180.21s.
- 2026-07-15T00:09:53+02:00 [DISCOVERIES] The first gate initially observed no analytics file because timed-out server calls continued after the client disconnected; the database appeared after late server completion. The runner now waits until both run-scoped DuckDB files exist and accept read-only connections. The rechecked Batch 1 gate passed with five search runs and 21,326 DEBUG log rows in the final export.
- 2026-07-15T00:09:53+02:00 [DISCOVERIES] Campaign analytics are incomplete despite usable MCP results: only 29/50 `search_runs` rows persisted, including 21 successful calls with no run row; all 166 `rerank_stages.status` values are null; one 1024D query embedding and 40 candidate embeddings persisted, but no `search_runs.payload_json` reported `query_embedding_dim`, so computed query-embedding coverage is undefined. Provider failures were concentrated in Gemma (58/58), Bright Data Yandex (28 errors + 1 incomplete/29), and SerpApi (16/29).
- 2026-07-15T00:09:53+02:00 [OUTCOMES] Result hygiene across 600 returned results: 0% within-query duplicates, 1% cross-query duplicates, 0.5% invalid URLs, 0% missing titles/snippets, 98.99% HTTPS, and 222 unique domains. The fixed 50-result manual sample scored 1.30/2 topical relevance, 1.04/2 research-goal usefulness, and 1.04/2 source quality; 15 missing ranks from three failed sample queries were scored zero. Raw, JSONL, DuckDB, summary, review, and 13 pandas Parquet datasets remain local under the ignored run directory.

## Query Embedding Propagation Fix — 2026-07-14
- 2026-07-14 [CODE] Fixed query embedding dropout in `ranking.py:95`: added `dc.query_embedding = list(ctx.query_embedding)` inside the `if ctx is not None:` block, immediately after the candidate embedding comprehension. The bi-encoder already produces the 1024-dim vector; the collector and persistence layer already accept it.
- 2026-07-14 [CODE] Added regression test `tests/test_query_embedding_propagation.py` with three test functions: (1) `test_rank_and_finalize_propagates_query_embedding_when_context_is_not_none` — collector state after `rank_and_finalize`; (2) `test_build_diagnostics_reports_query_embedding_dim_from_rerank_context` — `build_diagnostics` projection; (3) `test_persist_search_outcome_writes_query_embedding_row_when_context_is_not_none` — DuckDB write dispatcher via patched `analytics.duckdb_store.insert_query_embeddings` and `analytics.async_writes.dispatch_duckdb_write`.
- 2026-07-14 [TOOL] Verification: `ruff check` zero findings on `ranking.py` and `test_query_embedding_propagation.py`; `ruff format` applied; `pytest --basetemp=.pytest-tmp tests/test_query_embedding_propagation.py -v` — 3/3 pass.
## Retrieve Budget and Provider Health Removal — 2026-07-14

- 2026-07-14T10:08Z [DECISIONS] Runtime provider health/cooldown state is removed. Planning selects configured/reachable providers, and retrieval attempts every provider on every planned branch without consulting prior request outcomes.
- 2026-07-14T10:08Z [DECISIONS] The complete provider fan-out has one phase-level retrieve budget: `SEARCH_RETRIEVE_BUDGET_SECONDS`, default 30 seconds. Completed work continues to ranking; pending tasks are cancelled, drained, and recorded as `incomplete` / `retrieve_budget`.
- 2026-07-14T10:08Z [TOOL] Five-second live proof: `search.retrieve` completed in 5.027s with `retrieve_budget_exceeded=true`; completed providers contributed 15 final results and seven pending calls were recorded as `incomplete` / `retrieve_budget`.
- 2026-07-14T10:08Z [TOOL] Default-budget live proof: `search.retrieve` completed in 20.789s with `retrieve_budget_exceeded=false`; provider timeouts/errors remained ordinary `error` outcomes. Normal CLI wall time was 66.35s. DEBUG proof was 119.47s wall / 65.77s pipeline because `search.rank` expanded to 40.26s after a Hugging Face embedding timeout; the retrieve change reduces retrieval tail latency but does not by itself guarantee lower total CLI latency.

## Direct OpenAI Client Migration - 2026-07-14

- 2026-07-14 [CODE] Added Hugging Face Inference Providers/Nscale as the third worker: Cerebras -> Groq -> `openai/gpt-oss-120b:nscale` -> Vercel. The synchronous `InferenceClient` call runs off the event loop and remains bounded by the router timeout.
- 2026-07-14 [CODE] Removed the runtime LiteLLM dependency and migrated the remaining offline judge call to `openai.OpenAI` using the configured OpenAI-compatible endpoint.
- 2026-07-14 [CODE] OpenAI-compatible clients use explicit endpoint timeouts and `max_retries=0`; provider fallback remains in the orchestration router, preventing SDK retries from honoring a 60-second `Retry-After` inside a single attempt.
- 2026-07-14 [CODE] Replaced LiteLLM OpenInference instrumentation and doctor checks with the OpenAI instrumentor. `uv lock --offline` removed `openinference-instrumentation-litellm`.
- 2026-07-14 [NOTE] LiteLLM remains only as an optional transitive dependency of `mcpevals -> dspy`; removing it completely from the lock would require removing or replacing the existing eval extra.

## Fixed Six-Branch Provider Debugging — 2026-07-14

- 2026-07-14T02:06Z [CODE] Bright Data Yandex now uses the documented `https://www.yandex.com/search/?text=...&lr=...&lang=...` URL without `brd_json=1`; raw Yandex HTML is parsed from `li.serp-item`, with ad containers excluded.
- 2026-07-14T02:06Z [TOOL] Yandex parser contract smoke passed and Ruff passed for both Bright Data modules. Live Bright Data Yandex retrieval timed out before returning HTML, so provider-level Yandex success remains UNCONFIRMED.
- 2026-07-14T02:06Z [TOOL] `uv run hf spaces logs chmielvu/Web-Index` proved the Qdrant HTTP 500 occurred during an 18-second HF Space cold start. After Qdrant recovered `web_results` and began listening, the same direct provider probe returned five results.
- 2026-07-14T02:06Z [TOOL] Hugging Face `AsyncInferenceClient` with provider `hf-inference` returned a 1024-dimensional `intfloat/multilingual-e5-large-instruct` embedding; the previously observed `TypeError` is no longer reproducible with repo `huggingface_hub==1.11.0`.
- 2026-07-14T04:12Z [CODE] Fixed the live six-branch Qdrant `TypeError`: `provider_registry` injected the shared `query_embedding`, but `search_qdrant` did not accept it. The provider now consumes the precomputed vector and avoids duplicate Hugging Face embedding work.
- 2026-07-14T04:12Z [CODE] Search quality computation now runs after unified fact inserts inside the same dedicated DuckDB writer callback, eliminating the asynchronous read-before-write race.
- 2026-07-14T04:12Z [TOOL] End-to-end live proof persisted six ordered branch-role rows and a `search_quality_scores` row with `branch_count=6` and `total_final_results=15`; the focused shared-embedding Qdrant adapter probe returned five results.
- 2026-07-14 [CODE] Bright Data manufactured timeouts: `provider_catalog` 10s outer `retrieval._call_provider` envelope was shorter than `run_provider` (20s × 3 + backoff). Added `brightdata_provider_call_timeout_seconds()` (63s at defaults) for all three Bright Data providers; removed redundant `asyncio.wait_for` on Bing sidecar HTTP.

## Camoufox Content Restructure — 2026-07-13

- 2026-07-13T17:00Z [CODE] Steps 1–8 of the Camoufox content fallback plan implemented: moved 6 resolvers to content/resolvers/, renamed crawl4ai_client.py -> remote_clients.py with CamoufoxClient, replaced fallback.py with stages.py (4 stage functions), rewrote fetch_pipeline.py with Tier-2 orchestration, extracted specialized_pipeline.py, simplified batch_orchestrator.py to per-URL, simplified sitemap.py to Tavily-only, removed crawl4ai/playwright deps, added Camoufox settings/telemetry.
- 2026-07-13T17:00Z [CODE] VPS docker-compose: added `127.0.0.1:3000:3000` port publish to camoufox service, container recreated. WSL SSH tunnel: added `-L 3000:127.0.0.1:3000` to existing tunnel process. .env: added `CAMOUFOX_BASE_URL=http://127.0.0.1:3000`. Verified: /health returns {"status":"ok"}, /content returns HTML from example.com.
- 2026-07-13T17:00Z [CODE] Code review fixes: fixed content/__init__.py docstring (correct Tier-2 order), removed unreachable `raise` after retry loop in `remote_clients.py:CamoufoxClient.fetch_html`, ran ruff format on 3 files. CHANGELOG.md updated.
- 2026-07-13T17:00Z [TOOL] Verification: ruff check clean, ruff format --check clean, py_compile passes for all 4 changed files. Resolver tests: 30/30 pass. Stale sitemap tests: 13 fail (reference deleted legacy_sitemap module — Step 10). Import smoke blocked by `litellm.acompletion` import error at `llm/router.py:11`.

## Perplexity Cleanup + CLI Skill Refresh — 2026-07-13

- 2026-07-13T19:30Z [CODE] Removed all Perplexity search surface: `PerplexitySearchResponse` model + alias from `models.py`, `perplexity_search` from rate-limit `EXPENSIVE_TOOLS`, Perplexity Sonar steering message replaced with generic expensive-tool guidance in `expensive_tool_protection.py`, all Perplexity telemetry (attributes, metrics, records, re-exports) from `telemetry/`.
- 2026-07-13T19:30Z [CODE] Removed Perplexity/Pollinations references from `CLAUDE.md`, `README.md`, `middleware/AGENTS.md`, `prompts/AGENTS.md`. Updated `test_analytics_views.py` to use `grok_search` instead of `perplexity_search`.
- 2026-07-13T19:30Z [CODE] `skills/web-search-cli/SKILL.md` fully refreshed to match current CLI: added `--debug`, `sitemap generate`, `experiments`; removed `agent research`; updated `search web` (required `--research-goal`, `--num-results` 15–50, `--diagnostics`, removed `--provider`); added `--summary-mode`/`--focus-query` to `content batch`, `--backend` to `youtube transcript`.

## VPS Endpoint/Tunnel Audit — 2026-07-13

- 2026-07-13T13:02:55+02:00 [TOOL] Read `\\wsl.localhost\Ubuntu\home\an\projects\Contabo-Zawady\vps-services-manifest.md`: VPS SearXNG binds `127.0.0.1:8080`, DeGoog `127.0.0.1:4444`, Phoenix UI/OTLP HTTP `127.0.0.1:6006`, OTLP gRPC `127.0.0.1:4317`; only Qdrant is public.
- 2026-07-13T13:02:55+02:00 [DISCOVERIES] `.env` had `SEARXNG_BASE_URL=http://127.0.0.1:18080` and no DeGoog/Phoenix endpoint entries. The active WSL SSH tunnel was stale/partial, forwarding only the older service set; local `:4444` initially refused connections.
- 2026-07-13T13:02:55+02:00 [CODE] Corrected `.env` to `127.0.0.1:8080`, `127.0.0.1:4444`, and `127.0.0.1:6006/v1/traces`; added missing SSH forwards for `4444`, `8000`, `8765`, `8686`, and `4317` with keepalive/forward-failure options; updated Hermes job `7acbeb2b3573` prompt with the complete manifest forward list.
- 2026-07-13T13:02:55+02:00 [TOOL] Verification: SearXNG `/search` 200, DeGoog `/api/search` 200 using Bing-only engine, Phoenix `/healthz` 200, Phoenix `/v1/traces` 200; all manifest tunnel ports listen on localhost through WSL relay PID 16108. Focused provider/Phoenix tests: 22 passed.
- 2026-07-13T13:02:55+02:00 [CODE] Crawl4AI audit: manifest and client both use `/health` on port `11235`; `.env` now enables `CRAWL4AI_BASE_URL=http://127.0.0.1:11235`, and `get_crawl4ai_client().health_check()` returned true through the tunnel.
- 2026-07-13T13:02:55+02:00 [TOOL] Verified local `127.0.0.1:11235` listener via WSL relay PID 16108, `GET /health` returned HTTP 200, and focused content tests passed 8/8.
- 2026-07-13T13:58:04+02:00 [TOOL] Verified active profile cron record `~/.hermes/profiles/python-software/cron/jobs.json`: job `7acbeb2b3573` is enabled, `script=null`, `no_agent=false`, and its stored prompt contains the complete manifest forward list including Crawl4AI `11235`, SearXNG `8080`, DeGoog `4444`, Phoenix `6006`, and `4317`.
- 2026-07-13T13:58:04+02:00 [TOOL] Latest Hermes run reported all 10 requested ports open and both persistent SSH processes alive; the automatic recovery job is using the complete list, not only the supplemental five ports.

## Latency Fix — 2026-07-13

- 2026-07-13T00:50Z [DISCOVERIES] Root cause of 66s `search.rank` latency: Cerebras API returns 429 with `retry-after: 60` header. OpenAI client (used by LiteLLM) has built-in retry that respects the 60s header, blocking the event loop. LiteLLM's `num_retries=0` and `max_retries=0` kwargs do NOT disable the OpenAI client's internal retry — they only control LiteLLM's own retry layer. DuckDB logs confirmed: `search.rerank.summary` events with 63s durations on multiple runs.
- 2026-07-13T00:00Z [USER] Requested complete removal of the heavy experimental agentic research module from MCP.
- 2026-07-13T00:00Z [CODE] Deleted `src/kindly_web_search_mcp_server/agent/`, root `agent/brief.md`, the `agent` CLI command, agentic tool/catalog/telemetry/settings wiring and stale agent telemetry attributes, LangChain/LangGraph dependencies, and dedicated agentic tests.
- 2026-07-13T00:00Z [TOOL] Verification: targeted removal-contract tests passed (7); changed source files lint clean; compileall passed; full Ruff still reports three pre-existing unused imports in `contracts/base.py`. Broader affected tests retain pre-existing failures from missing optional `openinference.instrumentation.litellm`, stale DuckDB tests, and docstring drift.
- 2026-07-13T00:50Z [DISCOVERIES] Second bug: `rerank_stages` DuckDB table schema drift — `inserts.py` `_RERANK_STAGE_COLUMNS` includes `alpha_blend` column but the actual table (created by old schema) lacks it. Every `analytics_insert_rerank_stages` call fails with `BinderError: Table "rerank_stages" does not have a column with name "alpha_blend"`. Caught by try/except but wastes I/O.
- 2026-07-13T00:50Z [CODE] Fix 1: `llm/router.py` — wrapped `acompletion()` call with `asyncio.wait_for(timeout=effective_timeout + 5.0)` so the 60s Cerebras retry sleep is cancelled after the endpoint timeout. Router falls through to next provider (Groq) via the sequential ladder. Rerank latency dropped from 65s to 17s.
- 2026-07-13T00:50Z [CODE] Fix 2: `analytics/writers/schema.py` — added `_migrate_rerank_stages()` that runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for each new column. Called after `_ensure_rerank_stages()` in `ensure_store_schema()`.
- 2026-07-13T00:50Z [CODE] Trafilatura removed completely: `pyproject.toml` dep removed, `content/extract.py` rewritten to use BS4+markdownify, all references cleaned. `uv sync` uninstalled trafilatura + 9 transitive deps. Server startup dropped from 37s to 17s.
- 2026-07-13T00:50Z [TOOL] Verification: rerank 17.3s (was 65s), `provider: "groq"` (fell through from Cerebras 429), `alpha_blend` column present in DuckDB, ruff clean, all imports OK.

## Observability VSS Uplift — 2026-07-12

- 2026-07-12T20:35Z [USER] Approved `local://observability-vss-uplift-plan.md` (662 lines): clean-cutover observability redesign — 9 DuckDB fact/embedding tables, human-readable views, SearchDiagnostics/DiagnosticsCollector, per-phase diagnostics collection, non-blocking persistence, --diagnostics CLI, OTEL spans, DuckDB vss, 4D LLM-as-judge.
- 2026-07-12T20:35Z [CODE] Steps 1–2 done: `writers/schema.py` (9 tables + `ensure_vss_extension`), `table_names.py`, `inserts.py`, `core.py`, `views.py` (10 dashboard views). Stripped `observability_schema.py` to `provider_health_transitions` shim. Deleted `base_views.py`, `candidate_views.py`, `candidate_survival_views.py`, `derived_views.py`.
- 2026-07-12T20:35Z [CODE] Step 3 done: `search/diagnostics.py` (`SearchDiagnostics`, nested models, `build_diagnostics()`, `branch_outcome_preview`); `contracts.py` `DiagnosticsCollector` + `SearchRun.diagnostics` field.
- 2026-07-12T20:40Z [CODE] Step 4 done: instrumented `planning.py` (enrichment, intent, rewrite metadata, `search.plan` span), `retrieval.py` (per-provider calls with latency/status/URLs on `BranchOutcome.provider_calls`, `search.retrieve` span), `ranking.py` (merge_counts, rerank provider/model, embedding context, `search.rank` span), `service.py` (`total_latency_ms`). All collection is in-memory — no extra awaits on the hot path.
- 2026-07-12T20:40Z [USER] User requested non-blocking observability: no latency impact. Honored via: (1) `DiagnosticsCollector` writes are in-memory dict/list appends only; (2) `persist_search_outcome` uses `dispatch_duckdb_write` (fire-and-forget executor) not synchronous DuckDB; (3) candidate URLs capped at 32 per provider call; (4) candidate embeddings capped at 40 entries; (5) OTEL spans use `start_as_current_span` (SDK exports in background thread); (6) legacy synchronous `insert_search_run` replaced with dispatched write.
- 2026-07-12T20:45Z [CODE] Clean-cutover import chain repair: stripped `observability_inserts.py` to `insert_provider_health_transition` only; stripped `observability_tables.py`, `observability_rows.py`, `observability_store.py` to match; removed `insert_web_search_tool_call` + `build_response_result_rows` from `tools/_helpers.py`; removed legacy `insert_merged_candidates` from `merge.py`; removed `insert_query_understanding` calls from `understanding/resolver.py` (data now on `search_runs` columns); added `insert_query_embeddings` + `insert_final_results` + `insert_judge_evaluation` to `duckdb_store` and `writers/__init__` re-exports.
- 2026-07-12T20:50Z [TOOL] Import smoke: all modified modules import OK. Ruff: all checks pass. `test_server.py` failure is pre-existing (`openinference.instrumentation.langchain` not installed — environment issue, not code).
- 2026-07-12T20:50Z [DECISIONS] `BranchOutcome.provider_calls` tuple flows through `dispatch_branch` → `retrieve_branches` to avoid parallel writes to `run.diagnostics`. `branch_index` injected in `retrieve_branches` when building `branch_results`, not in `dispatch_branch` signature. `_rewrite_branches` returns `(branches, metadata)` tuple; `plan_search` unpacks and stores metadata on `dc.rewrite_metadata`.
- 2026-07-12T20:50Z [DISCOVERIES] GitNexus reported `insert_query_understanding` in `writers/core.py` but it was never created in Step 1 — the function existed only in the old `observability_inserts.py`. Understanding data (intent, confidence) is now captured on `search_runs` columns and `run.diagnostics`, making the separate insert redundant.
- 2026-07-12T21:00Z [CODE] Step 5 done: rewrote `outcomes.py` — `submit_search_outcome` now accepts `SearchRun` (not `SearchOutcome`), builds `build_diagnostics(run, total_latency_ms)` in the background task, writes 7 tables (search_runs, search_branches, provider_calls, search_candidates, final_results, query_embeddings, candidate_embeddings) via a single `dispatch_duckdb_write` (non-blocking). `rerank_stages` and `rerank_candidates` already written by `rerank/reporting.py` + `rerank/observability.py`. Added `merged_candidates` field to `DiagnosticsCollector`; `ranking.py` assigns `dc.merged_candidates = merged` for persistence. `service.py` now passes `run` to `submit_search_outcome` (not `run.snapshot()`). Added `return_run: bool = False` to `execute_web_search` for CLI diagnostics.
- 2026-07-12T21:05Z [CODE] Steps 6–7 done: `--diagnostics` flag added to `web_cmd`; `fetch_web_search_payload` accepts `diagnostics=True`, calls `execute_web_search(return_run=True)`, appends `_diagnostics` key to output payload. OTEL spans (`search.plan`, `search.retrieve`, `search.rank`) already implemented in Steps 4–5.
- 2026-07-12T21:05Z [CODE] Step 8 confirmed done (from Step 1): `ensure_vss_extension()` in `schema.py` installs/loads vss, creates HNSW indexes on embedding tables. `settings.vss_enabled` exists. Graceful fallback to brute-force `array_distance()` scans if vss unavailable.
- 2026-07-12T21:10Z [CODE] Step 9 done (subagent): `search_relevance_judge.py` upgraded from 1D to 4D. `JUDGE_SYSTEM_PROMPT` wired from `judge_prompt.py`. Pydantic models `DimensionScore` + `Judge4DResponse` defined. `SearchRelevanceResult` carries all 4 grades + scores, overall_score, rationale. `evaluate()` uses `complete_json(response_model=Judge4DResponse)`. `judge_runner.py` passes all 5 score columns + 4 grade columns + rationale to `insert_judge_evaluation`. LLM cascade unchanged.
- 2026-07-12T21:15Z [TOOL] Final verification: all modified files ruff clean. Import smoke passes for all 10 steps. Pre-existing ruff errors in `contracts/base.py` (unused pydantic imports) — not our changes. Pre-existing test failure in `test_server.py` (`openinference.instrumentation.langchain` not installed) — environment issue, not code.
- 2026-07-12T21:15Z [OUTCOMES] Observability VSS Uplift plan (662 lines, 10 steps) fully implemented. 9 DuckDB fact/embedding tables, 10 dashboard views, `SearchDiagnostics`/`DiagnosticsCollector`, non-blocking pipeline instrumentation, `--diagnostics` CLI flag, OTEL spans, `vss` extension, 4D LLM-as-judge. All observability is non-blocking: in-memory collection on hot path, `dispatch_duckdb_write` for persistence, URL/embedding caps, no extra awaits.


# CONTINUITY.md

Canonical session briefing for web-search-mcp. Updated as of 2026-07-12.

## Current State

- **Branch:** `main` (uncommitted changes in working tree)
- **Ruff:** `ruff check src/ tests/` passes after removing six unused CLI imports and fixing runtime typing imports.
- **Targeted overhaul tests:** 83 passed with repository-local `--basetemp=.pytest-tmp`; 13 third-party async cleanup warnings remained.
- **Manual smoke:** CLI schema passed; literal and news searches returned results, with pre-existing qdrant/HuggingFace warnings.
- **Full suite baseline:** not run for this overhaul.

## Provider Fanout / Phoenix Cutover — 2026-07-12

- 2026-07-12T12:12:42+02:00 [USER] Approved `local://provider-fanout-phoenix-plan.md` as the authoritative clean-cutover plan.
- 2026-07-12T12:12:42+02:00 [CODE] Added strict request/branch/provider contracts, the immutable 19-provider registry, deterministic enrichment and LLM rewrite planning, structured branch retrieval, BM25 scoring, detached outcomes, and one shared `execute_web_search` service used by MCP and CLI.
- 2026-07-12T12:12:42+02:00 [CODE] Replaced custom Phoenix export initialization with official `phoenix.otel.register`, explicit LiteLLM/LangChain/HTTPX instrumentation, local tunnel defaults, and ordered outcome/HTTP/telemetry shutdown.
- 2026-07-12T12:12:42+02:00 [TOOL] Focused new behavior suite passed 13 tests; CLI schema and doctor completed; live no-rewrite and rewrite-enabled searches both returned results through the shared service.
- 2026-07-12T12:12:42+02:00 [DISCOVERIES] `uv sync` is currently blocked because another process holds `.venv/Scripts/web-search-mcp.exe`; `uv lock` and direct dependency installs succeeded. Live searches still emit a pre-existing Hugging Face `AsyncInferenceClient.__del__` warning.

## CLI Debug Logging — 2026-07-10

- 2026-07-10T19:41Z [CODE] Added the global `web-search-cli --debug` flag. It configures the shared logging infrastructure at `DEBUG`, writes application logs to stderr, preserves JSON command output on stdout, and exposes `debug` plus the effective log level in response metadata.
- 2026-07-10T19:41Z [TOOL] Current CLI smoke checks succeeded: `web-search-cli schema`, `web-search-cli doctor`, and `web-search-cli --debug doctor`; the debug invocation returned `"log_level": "DEBUG"` and `"debug": true`.
- 2026-07-10T19:41Z [TOOL] Regression verification passed: `python -m pytest tests/cli/test_*.py` reported 27 passed; focused CLI/logging tests reported 19 passed; Ruff format and lint checks on all touched Python paths passed.

## CLI Search Latency Trace — 2026-07-10

- 2026-07-10T22:56+02:00 [TOOL] Read the complete DEBUG CLI trace and queried `duckdb_data/{analytics/search_events,logs/process_logs}.duckdb` for run `b5878b34-d76f-4d13-8781-11347e183d0a`. Persisted source-event timestamps show 39.734s in the search pipeline: 26.273s from query-understanding completion to completed rewrite plan, 9.626s for concurrent branches (slowest `composio_llm_search` 8.108s), and 2.881s for reranking.
- 2026-07-10T22:56+02:00 [CODE] The last commit newly executes RAKE extraction, Brave Autosuggest, and Brave Spellcheck sequentially before the Cerebras rewrite in `search/pipeline_builders.py`. The trace proves this new pre-rewrite block is the 26.273s segment, but lacks per-step timing, so the relative contribution of RAKE versus the two Brave calls is UNCONFIRMED.
- 2026-07-10T22:56+02:00 [TOOL] The 142.48s shell wall time included a 77.595s gap after `search.orchestrator.response` and before CLI JSON output. The test had concurrent DuckDB lock failures caused by the temporary server process started during debugging; [INFERENCE] `run_cli_async()` cleanup waited for blocked analytics writes. `cli/runtime.py` had only import-formatting changes in the last commit, so this post-pipeline gap is not attributable to that commit's source changes.

## MCP Startup Import Repair — 2026-07-10

- 2026-07-10T17:53:50Z [CODE] Deferred `agent.runner` until `agentic_web_research` executes and deferred `rake_nltk.Rake` until `keyword_extract._rake_extract()` executes; standard server startup no longer imports either optional runtime.
- 2026-07-10T17:53:50Z [DECISIONS] Telemetry and LiteLLM were intentionally left unchanged; the startup repair is limited to the two confirmed eager optional imports.
- 2026-07-10T17:53:50Z [TOOL] Targeted lazy-import regression tests passed (2); targeted Ruff check and format check passed; post-change MCP stdio `initialize` returned JSON-RPC `2.0` from `web-search` in 25.961s; post-change import profile contained no `rake_nltk` or `nltk` rows.

## Phase Two Brave Retrieval — 2026-07-10

- [CODE] `search_brave()` now calls Brave LLM Context (`/res/v1/llm/context`) and parses `grounding.generic` via `brave_common.py`.
- [CODE] Added `brave_news` specialized provider; `news` intent policy `1.1` includes `specialized_providers=("telegram", "brave_news")` with `brave_news` freshness args.
- [CODE] `ProviderExecutionPlan.specialized_provider_names` + `branch_planner` `specialized_original` branch wire specialized providers on the original query.
- [CODE] `BRAVE_GOGGLES_BY_INTENT` (default `{}`) merges goggles into `brave` / `brave_news` provider arguments at policy resolve time.
- [CODE] BrightData news Google URLs append `tbs=qdr:<token>` from intent freshness when `search_type=news`.
- [TOOL] Phase Two verification: 68 focused tests passed (66 component + `test_phase_two_pipeline` plan→branch integration for `news`/`general`); `ruff check` clean on changed paths; `web-search-cli doctor` import smoke OK after settings circular-import fix.

## Uncommitted Changes

The working tree has extensive changes from the codebase-rehab pass:
- `pyproject.toml` — added ruff/pytest/pyright config, removed redundant optional dev dep
- `server.py` → `server.py` + `tools/` package (12 modules)
- `telemetry.py` → `telemetry/` package (13 modules)
- `analytics/duckdb_store.py` → `analytics/writers/` package (9 modules) + thin facade
- `analytics/app.py` → `analytics/` subpackage (descriptions, app_queries, ui, tabs/)
- `middleware/session_tracking.py` — bug fix (SessionTracker race)
- `tools/search.py` — added `num_results` parameter (clamp 15-50)
- `search/__init__.py` — removed stale `telegram_client` import
- Test fixes: rerank_engines, rerank_core, sitemap, agent_steering, ab_integration, telegram
- Ruff format sweep on ~200 files

## Decisions

- 2026-07-18T17:20Z [DECISIONS] Batch content summaries (`batch_get_content`) now use a single Gemini API call with all URLs passed to the URL-context tool, instead of one call per URL. The batch path and its per-item fallback both use `GEMINI_SECOND_API_KEY` (paid key) to avoid rate limits. `get_content` (single-URL) is unchanged and continues to use `GEMINI_API_KEY`. GitNexus impact analysis was attempted but the subagent failed; manual review showed callers are limited to `batch_get_content` and `fetch_batch_content_payload`.

- DuckDB tests writing to repo root is **intentional** (analytics data persisted by design).
- `web_search` num_results clamped to 15-50 (not 1-25), default 15.
- Telegram tests that patch `search.telegram_client` fail because `telethon` is not installed (optional dep). Pre-existing, not a regression.
- `analytics/views.py` `vw_candidate_survival` test failure is pre-existing (test inserts provider event but view expects rerank_candidates data).

## Remaining Work

### Priority 1: Stale test sweep
~55 tests reference removed/renamed modules and fail at collection or runtime. Key offenders:
- `test_composio_tools.py` — `IMAGE_SEARCH_SLUG` removed
- `test_agentic_web_research.py` — `resolve_langfuse_credentials` removed, tool names changed
- `test_tool_descriptions.py` — docstring content drifted
- `test_analytics_views.py` — tool name `perplexity_search` → `grok_search`
- `test_observability_flow.py` — `kindly_` prefix purge
- `test_server.py` — `cache_stats` and `features_status` output shape changed
- `test_content_status_classifier.py` — assertion drift
- `test_youtube.py` / `test_youtube_api.py` — 9 failures from real network calls (patch bypass in full suite)

### Priority 2: Pyright error reduction
184 errors remaining. Many are optional-dep type stubs. Hot-path errors in server, pipeline, models should be fixed first.

### Priority 3: Deferred modularization
Files still over 500 lines:
- `settings.py` (698 lines) — dataclass with ~200 env knobs
- `search/pipeline.py` (591 lines) — core orchestrator
- `search/gemini_search_tool.py` (591 lines) — Gemini grounding client
- `content/github_discussions.py` (487 lines) — GraphQL client
- `content/fetch_pipeline.py` (476 lines) — 7-stage content resolver

### Priority 4: Commit checkpoint
The working tree has a large diff. Consider committing the rehab changes separately from the user's active dirty worktree changes (rerank/embedding/LLM/settings/qdrant).

## Debug Session 2026-07-06

### [DISCOVERIES]
- 2026-07-06T11:41Z [TOOL] `append_query_outcome_record()` is not the slow operation itself; in the labeled probe its worker ran for ~0.004s once it started, but the coroutine waited ~17.4s for a free `to_thread` worker.
- 2026-07-06T11:41Z [CODE] `analytics.writers.connection._LOCK` is a single global `threading.Lock` shared by all DuckDB writers.
- 2026-07-06T11:41Z [CODE] `analytics.observability_schema.ensure_pipeline_observability_tables()` is invoked on every observability insert and runs six `CREATE TABLE IF NOT EXISTS` statements under the same lock.
- 2026-07-06T11:41Z [CODE] `utils.observability.emit_observability_event()` persists `search.*`, `provider.*`, `rerank.*`, `tool.*`, and related prefixes to DuckDB, which explains the flood of `analytics.search_events` tasks in the threadpool trace.
- 2026-07-06T11:41Z [TOOL] The threadpool probe showed the awaited JSONL append queued behind long-running analytics DuckDB tasks, especially `analytics.provider_health_transitions`, `analytics.provider_candidates`, `analytics.branch_candidates`, `analytics.search_events`, `analytics.search_run`, and `analytics.final_results`.
- 2026-07-07T11:25:23+02:00 [TOOL] On a copied audit snapshot, `duckdb_data\\analytics\\search_events.duckdb` held 5,268 events across 535 runs from 2026-07-03T17:55:20 to 2026-07-07T09:04:14; top provider latencies still included `searxng` p95 ~9.2s, `brightdata` p95 ~10.0s, and `qdrant` p95 ~6.1s.
- 2026-07-07T11:25:23+02:00 [TOOL] Pre/post comparison around the 2026-07-06T13:18Z analytics-write fix showed `search_runs` average duration 24.3s before vs 55.9s after, but the after-fix runs also had higher result counts, so the latency change is not yet a like-for-like verdict.
- 2026-07-07T11:25:23+02:00 [TOOL] `search_quality_scores` looks partially drifted: after-fix rows are mostly present but report `total_final_results = 0` despite `search_runs.final_result_count = 15`, so the score table needs integrity checks before using it for conclusions.
- 2026-07-07T11:25:23+02:00 [TOOL] `process_logs.duckdb` held 17,024 rows across 86 loggers; after-fix error counts dropped from 200 to 50, but JSON-RPC parse errors and OTLP export failures remain active.

### [PROGRESS]
- 2026-07-06T11:41Z [TOOL] A control probe disabling observability persistence was launched, but the process did not complete within the 240s shell window, so the no-persist comparison is still UNCONFIRMED.
- 2026-07-06T11:41Z [TOOL] Current working hypothesis is that latency comes from analytics persistence fan-out plus the shared DuckDB writer lock, not from the providers, rerank fallback, or circuit breaker gates.
- 2026-07-06T13:18Z [CODE] Implemented a dedicated single-worker executor for DuckDB analytics writes in `analytics/async_writes.py` and shut it down during app lifespan teardown.
- 2026-07-06T13:18Z [TOOL] Post-fix culprit probe dropped `TOTAL` from 30.505s to 3.689s and reduced `append_query_outcome_record()` queue delay from 24.045s to 0.001s.
- 2026-07-07T11:25:23+02:00 [TOOL] Current analysis focus is to validate whether the fix improved end-to-end search behavior on matched query families rather than only reducing background writer contention, because the latest telemetry shows lingering provider and telemetry errors.
- 2026-07-07T12:45:11+02:00 [TOOL] Wrote `plans/duckdb-search-audit-report-2026-07-07.md` and `plans/duckdb-search-audit-brief-2026-07-07.md` after the DuckDB audit pass.
- 2026-07-07T14:26:49+02:00 [TOOL] Wrote `plans/mcp-search-quality-tech-debt-audit-2026-07-07.md` after cross-examining DuckDB results with provider planning, branch execution, rerank, judge, and analytics write code.

### [DECISIONS]
- 2026-07-06T13:18Z [CODE] Use a dedicated DuckDB write executor instead of the default asyncio executor for background analytics persistence so analytics fan-out cannot starve the response-path JSONL append.
- 2026-07-07T12:45:11+02:00 [TOOL] Treat the post-fix DuckDB comparison as workload-shifted cohort analysis, not a matched-query A/B test, because there is no exact query overlap between the windows.

### [DISCOVERIES]
- 2026-07-06T13:18Z [TOOL] After the dedicated executor change, `MAX_ACTIVE_WORKERS` in the probe fell from 12 to 3, showing the analytics write flood no longer occupies the default threadpool.
- 2026-07-06T13:18Z [TOOL] The remaining `index_final_results failed (non-fatal): AsyncInferenceClient.__init__() got an unexpected keyword argument 'provider'` message is still present in the probe log, but it no longer dominates latency.
- 2026-07-06T16:10:18+02:00 [TOOL] Live MCP probes over unrelated topics showed `qdrant` is not the only provider; result mixes varied by query and often came from `brave`, `ddg`, `serper`, `composio_llm_search`, and `search_router`.
- 2026-07-06T16:10:18+02:00 [CODE] `build_provider_execution_plan(intent='general')` includes `qdrant` in the default free-provider set alongside `searxng`, `ddg`, `gemma`, and `composio_llm_search`, so it is always present when enabled but not exclusive.
- 2026-07-06T16:10:18+02:00 [CODE] `search/qdrant.py` and `index/web_results_index.py` both use the same `collection_name="web_results"` with no query- or intent-level filter, so the read provider can surface unrelated previously indexed pages.
- 2026-07-07T12:45:11+02:00 [TOOL] The audit snapshot had 5,268 analytics events across 535 runs and 17,024 process logs; after the fix, `brightdata` improved from 100% provider-call errors to 47.6%, `qdrant` dropped to 0% errors, and `searxng` still failed on every call.
- 2026-07-07T12:45:11+02:00 [TOOL] `search_quality_scores` is currently untrustworthy after the fix because scored rows mostly report `total_final_results = 0` even when `search_runs.final_result_count = 15`.
- 2026-07-07T12:45:11+02:00 [TOOL] The post-fix workload is heavier and broader: average query length rose from 5.0 to 8.6 words, and rewrite-enabled searches dominated the after-fix cohort.
- 2026-07-07T14:26:49+02:00 [CODE] `search_quality_scores` false-zero rows are explained by async DuckDB writes: `pipeline.py` queues `search_run` and `final_results`, then immediately fire-and-forgets `compute_search_quality()`, which reads DuckDB before queued rows are guaranteed durable.
- 2026-07-07T14:26:49+02:00 [CODE] `final_results.providers`, `provider_count`, and `entities_count` are never passed as top-level insert args in `search/pipeline.py`; all 1,543 audited final-result rows had those normalized columns null.
- 2026-07-07T14:26:49+02:00 [CODE] Historical audit finding (superseded by the 2026-07-10 explicit target routing): provider planning prepended BrightData whenever configured and sharded providers across original/rewrite branches, so earlier comparisons were non-comparable.
- 2026-07-07T14:26:49+02:00 [CODE] Historical audit finding (superseded by the 2026-07-10 acceptance gate): the default `bi_cross_llm` path ran the LLM stage on normal search, and a failed LLM outcome could be accepted when only its fallback output count was nonzero.

## Outcomes

- 2026-07-07T12:45:11+02:00 [TOOL] Completed a full DuckDB audit writeup with one detailed report and one short brief in `plans/`, focused on latency, quality, provider stability, telemetry integrity, and the recent fix verdict.
- 2026-07-07T14:26:49+02:00 [TOOL] Completed second-pass tech-debt/code cross-examination report in `plans/`, recommending measurement repair first, then provider-health-aware planning, then rerank tail-latency controls.

## Live Debug Session 2026-07-07

### [DISCOVERIES]
- 2026-07-07T18:03:42+02:00 [TOOL] Live `web_search` probe took ~29.1s before this pass; latest direct patched pipeline probes complete in ~19.7-20.0s with rewrite enabled, 15 final results, and the active rerank path intact.
- 2026-07-07T18:03:42+02:00 [CODE] `search/provider_dispatch.py` called `asyncio.wait()` on an empty pending set after an early provider completion, raising `ValueError: Set of Tasks/Futures is empty` and discarding a branch that had already produced BrightData results.
- 2026-07-07T18:03:42+02:00 [CODE] `rerank/core.py` skipped the bi-encoder stage for normal 15-result searches because the gate required `len(candidates) > top_k * 2`; a live run with 23 candidates therefore ran cross/LLM/diversity but not bi-encoder.
- 2026-07-07T18:03:42+02:00 [CODE] `search/brightdata.py` was not treating Bing as a sidecar in practice: after Google returned results, the function still awaited/cancel-drained the optional Bing task long enough to create 10s+ BrightData tails.
- 2026-07-07T18:03:42+02:00 [CODE] `search/gemma_serp.py` contained a hardcoded Google AI API key that Google reported as leaked; the provider now reads configured Gemma/Gemini keys instead. Current configured Gemma/Gemini keys may still be rejected upstream; UNCONFIRMED until rotated or separately validated.
- 2026-07-07T18:03:42+02:00 [CODE] LiteLLM background success logging serialized full `ModelResponse` objects and emitted Pydantic serializer warnings; the router now uses LiteLLM quiet/no-log flags plus a targeted filter for that third-party warning.
- 2026-07-07T18:03:42+02:00 [CODE] `analytics/quality_metrics.py` opened the analytics DuckDB file with `read_only=True` while writer connections used normal mode, causing background `compute_search_quality` connection warnings.
- 2026-07-07T18:49:27+02:00 [TOOL] Fresh MCP stdio subprocess proof `OpenTelemetry LiteLLM reranking latency debugging unique MCP tool probe 2026 0707 corrected final proof` returned 15 results in 22.082s with `warnings=null` and zero stderr bytes after unwrapping the MCP SDK top-level `result` payload.
- 2026-07-07T18:49:27+02:00 [TOOL] Direct pipeline proof `OpenTelemetry LiteLLM reranking latency debugging unique MCP tool direct pipeline 2026 0707 async rerank analytics proof` returned 15 results in 11.596s with BrightData/DDG/Search Router and persisted rerank stages: bi_encoder 41->41 in 5060ms, cohere_fast 41->41 in 371ms, llm_rerank 41->20 in 960ms, diversity 20->20 in 7ms.
- 2026-07-07T18:49:27+02:00 [CODE] Short-lived MCP subprocesses can still lose late analytics rows after the response when the harness closes stdio immediately; direct pipeline with explicit analytics drain persisted search_run, rerank_stages, rerank_candidates, and final_results 15/15.
- 2026-07-07T22:20:00+02:00 [TOOL] CLI search runs were silently dropping DuckDB write events on process exit because CLI command execution did not run under a lifespan context and exited before background thread pool futures could execute.
- 2026-07-07T22:20:00+02:00 [CODE] Pruned stale commands `ai perplexity` and `images search` from `skills/web-search-cli/SKILL.md` as they are no longer supported.
- 2026-07-07T22:20:00+02:00 [CODE] Verified `huggingface_hub` package was updated in `.venv` to version `1.11.0`, resolving the `TypeError` related to `provider` parameter on older versions.

### [PROGRESS]
- 2026-07-07T18:03:42+02:00 [CODE] Patched provider dispatch to guard empty waits, remove drained early-cancel tasks from the active set, and keep normal early cutoffs at info level.
- 2026-07-07T18:03:42+02:00 [CODE] Patched rerank so bi-encoder runs when candidate count exceeds `top_k`, and diversity does not re-fetch candidate embeddings after an upstream bi-encoder embedding failure.
- 2026-07-07T18:03:42+02:00 [CODE] Patched BrightData Bing sidecar collection with `BRIGHTDATA_BING_JOIN_GRACE_SECONDS` defaulting to 0.25s and non-blocking cancellation; direct BrightData provider probe improved from ~10-14s to ~1.3-2.3s on the same query family.
- 2026-07-07T18:03:42+02:00 [CODE] Patched Gemma provider credential sourcing, LiteLLM response-warning suppression, successful bi-encoder timing log level, and DuckDB quality-metric connection mode.
- 2026-07-07T18:03:42+02:00 [TOOL] Verification so far: `py_compile` passes for touched runtime modules; direct full pipeline with explicit background cleanup returned 15 results in 19.73s with `warnings=null` and no stderr warnings. Live long-running MCP process still needs restart/reprobe to load these code changes.
- 2026-07-07T18:49:27+02:00 [CODE] Moved rerank candidate-survival analytics off the response path by dispatching batched rerank candidate writes through the dedicated DuckDB write executor instead of awaiting `asyncio.to_thread()` inside each rerank stage.
- 2026-07-07T18:49:27+02:00 [CODE] Added analytics background-task drain plus immediate DuckDB executor submission so shutdown does not cancel writer coroutines before they submit work; removed invalid OpenTelemetry `None` token attributes from non-LLM rerank stage events.
- 2026-07-07T18:49:27+02:00 [TOOL] Verification: `py_compile` passed for 23 touched runtime modules; `ccc index` completed with 3,663 chunks / 466 files / 0 indexing errors. `ruff` is unavailable in the repo venv (`ruff.exe` missing and `python -m ruff` not installed), so lint was not run.
- 2026-07-07T22:20:00+02:00 [CODE] Implemented `run_cli_async` helper that drains background tasks and cleanly shuts down the DuckDB write executor before CLI exit, resolving the silent write drops.
- 2026-07-07T22:20:00+02:00 [CODE] Added `--domain-boost` and `--domain-block` arguments to `web-search-cli search web` to achieve parity with the MCP `web_search` tool parameters.
- 2026-07-07T22:20:00+02:00 [TOOL] Successfully ran and verified CLI searches in the virtual environment. Search runs are now properly persisted to `search_runs` database with all 26 CLI tests and 27 targeted tests passing.

## Audit Microsite

Served at http://localhost:8765 (if running). Static HTML at `test-results/migration-audit/index.html`.

## Recovered History (lines 651-696 — the rest was lost when the file was overwritten)

> **NOTE:** Lines 1-649 of the original CONTINUITY.md were overwritten and cannot be recovered. The entries below are from lines 650-696 that were read during the 2026-07-05/06 session.

- 2026-05-11T13:04:01+02:00 [USER] Requested FastMCP documentation exploration via crawl4ai/Kuzu MCP and advice on better client steering/tool chaining for this MCP server; research/advice only, no implementation requested.

- 2026-05-11T13:10:17+02:00 [TOOL] Follow-up FastMCP doc pass read official pages for server, pagination, prompts, context, resources/templates, CodeMode, ResourcesAsTools, and PromptsAsTools.

- 2026-05-11T13:42:00+02:00 [CODE] Saved FastMCP client-steering recommendations to plans/FastMCP-client-steering-plan.md; registered Codex global MCP server kindly-web-search.

- 2026-05-11T14:07:27+02:00 [TOOL] Corrected Codex MCP registration for kindly-web-search after startup failure.

- 2026-05-11T14:45:00+02:00 [TOOL] Live kindly-web-search MCP tool testing found: default web_search can return severe SearXNG noise for exact FastMCP docs queries; composio_similarlinks is strong for expanding from known-good URL.

- 2026-05-11T14:40:36+02:00 [TOOL] Added Codex global MCP server `grafana` using official Grafana Codex guidance.

- 2026-05-11T14:46:11+02:00 [TOOL] Grafana MCP observability review found concrete pipeline issues: Loki had only one app log and it was an OTEL span export KeyError.

- 2026-05-11T14:51:17.6662653+02:00 [CODE] Fixed local SearXNG deployment/config for web-search MCP live testing: repo config now uses SearXNG use_default_settings.engines.keep_only with 12 curated engines.

- 2026-05-11T15:17:30.4944260+02:00 [CODE] SearXNG practitioner/live tuning completed for local MCP search: final repo-managed profile uses keep_only with 16 enabled engines.

- 2026-05-11T15:19:52.4706753+02:00 [CODE] Superseded prior 16-engine SearXNG profile: removed Crossref after post-query logs showed a timeout.

- 2026-06-02T13:53:04+02:00 [CODE] Implemented the agentic web research layer in a new top-level `agent/` package: LangChain/LangGraph ReAct runner with NanoGPT.

- 2026-06-14T20:15:00+02:00 [CODE] Rebuilt the existing HF Space `chmielvu/Web-Index` in place. The remote repo now listens on port 7860.

- 2026-06-15T03:11:35+02:00 [CODE] Fixed the telemetry gap behind the Grafana dashboard miss: top-level search request/duration metrics now emit from the pipeline.

- 2026-06-15T08:04:11+02:00 [CODE] Reviewed the large YouTube/LLM/rerank change set, fixed a Gemini grounding retry crash, restored PollinationsClient base_url injection.

- 2026-06-30T12:35:00+02:00 [DISCOVERIES] The 120s `web_search` timeout is driven by stacked sequential orchestration, not query fanout alone.

- 2026-07-03T00:00:00+02:00 [TOOL] Isolated harness verification confirmed `492381b` changed branch execution as intended.

- 2026-07-03T00:00:00+02:00 [TOOL] Isolated BrightData harness verification showed `_search_bing()` still swallows `asyncio.CancelledError`.

- 2026-07-03T00:00:00+02:00 [CODE] Restored the shared `utils/task_scope.py` compatibility surface, removed the stale CancellationToken import.

- 2026-07-03T00:00:00+02:00 [CODE] Corrected the earlier cleanup mistake: `utils/task_scope.py` was deleted again, `provider_dispatch.py` now uses direct asyncio.wait / cancel / drain logic.

- 2026-07-03T16:08:58+02:00 [TOOL] Scira rerank is mostly tool-local, not mode-global.

- 2026-07-06T02:00:00+02:00 [CODE] Codebase rehab pass: ruff 0 errors (was 71), pyright 0 errors (was 184), full suite 719 passed / 17 failed / 31 errors (was 682/54/31). Modularized server.py (2211→416L), telemetry.py (2272→13 files), duckdb_store.py (1347→183L), analytics/app.py (735→91L). Fixed stale tests for removed modules. web_search num_results clamp set to 15-50. SessionTracker race bug fixed.
- 2026-07-06T00:00:00+00:00 [CODE] Committed full working tree snapshot as `95fd286` and pushed `main` to `origin` successfully; commit includes the modularized server/tooling split plus repo-local generated artifacts that were present in the worktree at commit time.
- 2026-07-06T00:00:00+00:00 [CODE] Cleanup commit `50697bd` removed accidental generated artifacts from the repo snapshot: `MagicMock/` test outputs, `scripts/diagnose_embedding_latency.py`, and two generated `.duckdb` files. `.repomixignore` and `repomix.config.json` were restored because they were intended to stay.
- 2026-07-06T04:08:04+02:00 [CODE] Renamed the public sitemap tool to `generate_sitemap` and switched it to Tavily Map first with legacy Crawl4AI sitemap fallback. Added `content/tavily_map.py`, split the old semantic sitemap logic into `content/legacy_sitemap.py`, and updated the CLI/tool catalog/server wiring plus focused regression tests.

## Breaking Query Rewrite and Rerank Overhaul — 2026-07-10

- 2026-07-10 [CODE] Query branching now has explicit `original_free` → `free`, `keyword_refined` → keyword/SERP, and `neural_refined` → neural routing. Literal search syntax bypasses the LLM rewrite to preserve operators; specialized community providers remain intent-policy selections rather than branch targets.
- 2026-07-10 [CODE] Rewrite preprocessing combines caller terms with RAKE-NLTK phrases extracted from `research_goal`. Brave Autosuggest is best-effort, sends `rich=true`, and uses only the separate `BRAVE_SUGGEST_API_KEY`; Brave Spellcheck uses `BRAVE_API_KEY`.
- 2026-07-10 [CODE] Branch outputs are filtered by the DuckDB-backed `blocklist_patterns` store and cached compiled matcher before merge. Merge uses pure rank-based RRF with intent-specific `rrf_k` (`news=35`, `digital_humanities=70`, otherwise `60`); provider/list weights are removed from scoring and execution plumbing.
- 2026-07-10 [CODE] The Qwen XML listwise-CoT reranker now escapes untrusted title/URL/snippet fields, deterministically shuffles display IDs and remaps them, parses only `<final_ranking>`, and uses normalized linear ordinal scores.
- 2026-07-10 [CODE] Rerank acceptance requires an error-free LLM outcome with non-empty relevance scores. Bi-encoder and cross-encoder stage multipliers provide monotonic narrowing, the LLM receives the remaining candidates, and diversity is terminal with no tail concatenation.
- 2026-07-10 [TOOL] Verification ran `ruff format src/ tests/`, full Ruff (pass), 18 overhaul tests (pass), 62 migrated contract tests with repository-local basetemp (pass), CLI schema, two live CLI searches, Brave Autosuggest mock, RAKE extraction, and DuckDB blocklist lifecycle.

## Recovery and MCP Entrypoint — 2026-07-10

- 2026-07-10T18:54Z [TOOL] Recovered seven Phase One untracked source files from exact `write` payloads in the OMP session transcript at `C:/Users/Jan/.omp/agent/sessions/-Documents-GitHub-1Agents1-.CLI-web-search-mcp/2026-07-10T08-12-52-214Z_019f4b16-2c74-7000-96b6-7db8762478f3.jsonl`; replayed the recorded keyword and blocklist edits.
- 2026-07-10T18:54Z [CODE] Restored `search/{blocklist,keyword_extract,literal_passthrough,query_rewrite_preprocess}.py` and `prompts/rewrite/{__init__,base,intents}.py`; restored Phase Two `search/{brave_common,brave_news}.py` and focused tests.
- 2026-07-10T18:54Z [CODE] Fixed the MCP `python -m kindly_web_search_mcp_server.server --transport stdio` entrypoint by calling `main()` under `if __name__ == "__main__"`; this command previously imported then exited code 0 without starting stdio transport.
- 2026-07-10T18:54Z [TOOL] Full DEBUG stdio probe received an MCP `initialize` JSON-RPC response from `web-search` and confirmed the server remains alive; restored-module checks: Ruff clean, DuckDB blocklist lifecycle passed, 26 focused rewrite/branch/Brave tests passed.
