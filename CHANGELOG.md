# Changelog

## [Unreleased]
### Added - FlockMTL automatic judgment pipeline
- **`analytics/judges.py`** — new orchestrator (`judge_search_run(run_key)`) that runs FlockMTL prompts on every completed search and persists verdicts to `llm_judgments`. Three judgment kinds: `classify_failure` (failed runs only), `grade_relevance` (once per final result), `judge_rewrite` (once per planner rewrite variant — replaces the rejected semantic_dedup idea; row-per-variant shape matches `grade_relevance` for clean vw aggregations).
- **`schedule_judge_search_run(run_key)`** — fire-and-forget wrapper on a thread pool (`ThreadPoolExecutor(max_workers=4)`). Wired into `search/outcomes.py::submit_search_outcome` so the judge runs after every search without blocking the user-facing response.
- **`llm_judgments` table** — persisted audit trail (recorded_at, run_key, judgment_kind, judgment_target, prompt_name, model_name, verdict, input_tokens, output_tokens, duration_ms, status, error_message, payload_json). Created in `analytics/writers/schema.py::_ensure_llm_judgments`.
- **`search_runs.rewritten_branch_queries VARCHAR[]`** — dedicated column for the 4 planner rewrites (k1, k2, k3, neural), distinct from `search_branches` which holds the 6-branch dispatched topology. Populated from `SearchPlan.rewrite_queries` (new field on `contracts.SearchPlan`).
- **Two safe views** in `analytics/views.py`:
  - `vw_llm_judgments` — read-only mirror of `llm_judgments`, ordered by recency. NO per-row `llm_complete` calls (refresh is free).
  - `vw_flockmtl_resources` — introspection over the `flockmtl_resources` catalog (which MODELs + PROMPTs are registered).
- **`flockmtl_resources` metadata table** — tracks registered resources (FlockMTL has no built-in catalog introspection; `duckdb_models()` / `duckdb_prompts()` do not exist). Backs `vw_flockmtl_resources`.
- **Mock judge server** (`scripts/mock_judge_server.py`) — OpenAI-compatible HTTP server with deterministic keyword-based scoring for offline FlockMTL integration tests. Returns `{"items": [{"verdict": ...}]}` shape per flock's `ExtractCompletionOutput` (verified against the flock source).

### Changed
- **Default `FLOCKMTL_ENABLED` flipped to true** in `settings.py:250`. The integration is on by default; set `FLOCKMTL_ENABLED=false` to disable.
- **`SearchPlan`** in `search/contracts.py` gained `rewrite_queries: tuple[str, ...]` (default `()`) and the `create()` classmethod now accepts it. `planning.py::plan_search` populates from `_rewrite_queries()` output (only when the rewrite path succeeded; otherwise empty tuple so the judge skips rewrite rows).
- **`search/outcomes.py`** writes the new `rewritten_branch_queries` column from `outcome.plan.rewrite_queries` instead of from the dispatched-branch list (the old `payload_json["rewritten_branch_queries"]` shape was the 6-branch topology, NOT the 4 planner rewrites — the most important bug caught during smoke test).
- **`analytics/writers/inserts.py::_SEARCH_RUN_COLUMNS`** extended with `rewritten_branch_queries` (between `rewrite_error` and `payload_json`).
- **`flockmtl` integration into `ensure_store_schema`** is split:
  - `ensure_flockmtl_loaded(connection)` — `INSTALL` + `LOAD` only. Network-bound, no DB writes, safe outside `_LOCK` on a short-lived pre-lock connection.
  - `ensure_flockmtl_resources(connection)` — `CREATE MODEL`/`CREATE PROMPT` DDL + writes to `flockmtl_resources`. Holds `_LOCK`, self-sufficient (does its own `LOAD`).
  - `ensure_flockmtl(connection)` — convenience wrapper for `web-search-cli doctor`.

### Fixed
- **`flockmtl_setup.py` deleted** — superseded by `writers/connection.py::ensure_flockmtl_*` (no functionality loss; the old module was unimported dead code per the e07ca83 origin audit).


### Added — FlockMTL LLM-as-Judge refinement (six-facet decomposition + calibration harness)
- **Six facet-decomposed judgments** replacing the three legacy prompts. Each facet has a Prometheus scaffold (reasoning BEFORE `[RESULT]` token, anchored rubric, structured output) and a per-facet blindness rule (G-Eval self-enhancement bias mitigation):
  - **`judge_run_overview`** (1 call/run, fires FIRST) — holistic good/mixed/bad verdict + `analysis` + `recommendations[]` + `confidence` 1-4. `###Scope note:` section forbids reranker scores; the digest that the orchestrator builds (`_build_run_digest`) is a whitelist SELECT (ranks/titles/links/counts/stage-names only, no `final_score`/`llm_raw_score`/`cross_encoder_raw`/`fused_score`/`hybrid_rrf_score`).
  - **`judge_intent_coherence`** (1/run) — intent matches query + research_goal.
  - **`judge_rewrite_coverage`** (1/run iff `rewrite_enabled` && rewrites non-empty) — counts distinct retrieval facets across the 4 planner rewrites.
  - **`judge_rerank_improvement`** (1 per `rerank_stages` row) — positional only (rank_before/rank_after/survived/link); NO reranker scores.
  - **`judge_result_quality`** (1 per `final_results` row, ≤15/run) — snippet-only `intent_match` YES/NO + `informativeness` 1-4 + `confidence`. Blindness: no `final_score`/reranker scores. SELECT is an explicit whitelist (rank, title, link, snippet only) — never `SELECT *`.
  - **`judge_failure_cause`** (1/run iff `status != 'success'` OR `final_count == 0`) — PollMultihop few-shot root-cause triage (5 examples: 2 mined from real failure rows; 3 marked placeholders to be replaced when real `no_results`/`irrelevant_sources`/`rerank_error` failures accumulate).
- **`llm_judgments` schema extended** with five audit columns: `facet VARCHAR`, `reasoning VARCHAR`, `rubric_version VARCHAR NOT NULL DEFAULT 'v1'`, `confidence SMALLINT`, `context_shown JSON`. Migration handled by `_ensure_columns` idempotent-ALTER inside `ensure_store_schema` so existing production DBs pick up the new columns on next bootstrap.
- **Two new calibration tables** (created by `_ensure_judge_rubrics` / `_ensure_judge_calibration_set`): `judge_rubrics(rubric_version, facet, model_name, prompt_name, fewshot_json, is_active, kappa_score, created_at)` and `judge_calibration_set(run_key, facet, model_name, human_verdict, judge_verdict, adjudicator, adjudicated_at, rubric_version)`. PRIMARY KEY on `(rubric_version, facet, model_name)` and `(run_key, facet, model_name)` respectively, so κ upserts per cell are safe.
- **`analytics/judges.py` rewrite** — `judge_search_run` body now fires the six facets in canonical order. Added `_parse_result(raw)` (regex-split on `[RESULT]` token, with fallback to first `{...}` block; returns `dict | None`), `_store_judgment_row(...)` (common success-or-error-row path; uses the `_parse_result` shape to populate compact `verdict` + reasoning + confidence), `_build_run_digest(connection, run_key)` (one-string compact digest for the overview; SELECT whitelists; never leaks banned scores), `_fetch_branch_errors(connection, run_key)` (JOIN onto `provider_calls.error_type` via `branch_index` because `search_branches` has no `error_type` column), `_format_overview_reasoning(parsed)` (analysis + numbered recommendations block). Banned score names are kept in a single `_BANNED_RERANK_SCORES` constant referenced by both the orchestrator comments and the new tests.
- **`_JUDGE_MODEL` module-level selector** in `analytics/judges.py` (default `"judge_quality"`). Production callers leave this at default; the calibration harness (`analytics/judge_calibration.py`) rebinds it to `"judge_fast"` to fire the A/B pass and restores in `finally`. All six facet blocks in `judge_search_run` read this selector, so no signature changes.
- **`analytics/judge_calibration.py` extended** with the periodic DoorDash "calibrate" loop: `compute_kappa(human, judge, *, ordinal=False)` (pure-Python Cohen's κ or linear-weighted κ — no scipy dependency), `run_calibration(golden_run_keys, *, rubric_version='v1', db_path=None)` (runs the six facets with both models, joins to `judge_calibration_set.human_verdict`, upserts κ into `judge_rubrics`), and a `python -m kindly_web_search_mcp_server.analytics.judge_calibration --golden rk1 rk2 ... [--rubric-version v1]` CLI that prints a per-facet per-model κ table.
- **`vw_llm_judgments` view** extended with `facet`, `reasoning`, `rubric_version`, `confidence`, `context_shown` so dashboard queries see the audit trail.
- **New `vw_judge_facet_agg` view** — per-day, per-facet, per-model, per-`rubric_version` aggregates: `total_rows`, `success_rows`, `success_rate`, `avg_confidence`, `median_confidence`. Deliberately facet-grained (NOT collapsed to a single run-quality score) — honors the DoorDash canon that a single score hides actionable failures. `ensure_views` registers it through the existing `_build_dashboard_view_sql` loop with no new wiring.
- **`tests/test_judges_facets.py`** — 13 new tests across three plain-pytest classes (`TestJudgeFacets` / `TestParseResultAndErrorPath` / `TestScheduleSignaturePreserved`): six facet-count/blindness tests + four `_parse_result` unit tests + one parse-failure storage path + two `schedule_judge_search_run` contract tests. Monkeypatches `judges._run_prompt` (canned per-facet responses recording every call) and `judges._ensure_loaded` (returns True — no FlockMTL install/load).

### Decisions
- **Model assignment (user-confirmed)**: `judge_quality` (`mistral-small-2506`, the 120B) for all six production facets INCLUDING the holistic overview. `judge_fast` (`ministral-3b-2512`, the 3B) ONLY in the calibration A/B harness. If 120B per-result cost grows, demote only `judge_result_quality` to `judge_fast`; the calibration A/B will already have measured the per-facet κ gap. The overview stays on the 120B regardless (it is 1 call/run and is the dashboard-triage headline).
- **Overview is additive, not a replacement**: `judge_run_overview` sits alongside the five diagnostic facets. DoorDash canon (a single score hides actionable failures) is preserved because the five facets still localize each pipeline-transition failure; the overview is the dashboard headline on top.
- **Per-result evaluation (no sampling)**: all final results (≤15) judged per run, every run. Low personal volume makes this affordable.
- **Snippet-only groundedness**: `judge_result_quality` judges snippet-level `intent_match` + `informativeness`, NOT true page-grounded RAG Triad groundedness. Page bodies are not persisted per result; deferring the persisted-groundedness upgrade.
- **`rubric_version` policy**: every judgment row stamped with `'v1'`. Prompt names are unversioned for v1. A prompt change = orchestrator bumps to `'v2'` AND renames the FlockMTL prompt (e.g. `judge_intent_coherence_v2`) so both coexist (FlockMTL `CREATE PROMPT` cannot overwrite). `vw_judge_facet_agg` filters by `rubric_version` for trend comparisons.
- **Secret re-registration policy (still active for the new prompts)**: FlockMTL's `__default_openai` secret is re-registered per-connection (non-PERSISTENT). API keys live in env vars / settings, never on disk. PERSISTENT secrets (which would write unencrypted key material to `~/.duckdb/secrets/`) were rejected for this reason — Option C (per-connection re-register) was chosen over Option A (PERSISTENT) per the safety review. `_ensure_flockmtl_secret` is still called inside the new six-facet orchestrator on every fresh connection.

### Verified
- `uv run pytest tests/test_judges_facets.py tests/test_judge_after_outcome_write.py -v` → 18/18 pass (13 new + 5 existing scheduling tests).
- `uv run ruff check` on all 6 touched files → clean.
- `uv run python -m py_compile` on all touched files → clean.
- Fresh-DB bootstrap creates `judge_rubrics`, `judge_calibration_set`, and `llm_judgments` with the 5 new audit columns.
- `vw_llm_judgments` and `vw_judge_facet_agg` both resolve against a fresh DB and return correct per-facet aggregates.
- Digest blindness audit: `banned_scores not in build_run_digest(...)` against an in-memory DB seeded with `final_results.final_score=0.9` and `rerank_candidates.llm_raw_score=0.9, fused_score=0.8` returns empty (no leak).
- `_parse_result` handles all 5 input shapes: valid JSON after `[RESULT]`; valid JSON with trailing commentary; malformed JSON; missing `[RESULT]` marker; `None`/empty.
- `compute_kappa` returns canonical values: binary perfect → 1.0; binary full disagreement (3 cats) → -0.8; total disagreement (4 cats) → -0.333; empty → 0.0; non-trivial ordinal → 0.2.
- **Live smoke against Mistral API (`web-search-cli search web --query ... --rewrite`) is UNVERIFIED in this session.** `MISTRAL_API_KEY` is present in the environment but returns 401 Unauthorized on a direct minimal chat-completion probe, so the orchestrator's end-to-end production path (FlockMTL `llm_complete` → mistral-small-2506 → row persistence) was not exercised live. Code path is internally verified via mocked `_run_prompt` end-to-end (see `test_judges_facets.py::test_run_overview_fires_first_per_run` and the result-quality blindness tests that capture `context_columns`); the live round-trip must be re-run once a valid key is available.

## [Unreleased - earlier]
### Fixed — Critical bugs from live-testing evaluation
- **Summary JSON truncation (`EOF while parsing a string`)**: Upstream Gemini API bug (googleapis/python-genai#2062) — `max_output_tokens` is a combined think+output budget on Gemini 3 models, not output-only as documented. With `thinking_level="high"`, the model filled ~96% of the budget with thinking tokens, leaving JSON output truncated mid-string. Fixed by removing `thinking_config` from both `_make_config()` and `_make_batch_config()` in `content/summary_backend.py`.
- **Windows `STATUS_ACCESS_VIOLATION` on parallel `web_search` calls**: Two thread-safety issues in native code paths. (1) `rake_nltk` → `nltk` → `scipy` ran via `run_in_executor()` in a separate OS thread while `bm25s` → `scipy.sparse` ran on the event loop — two threads entering OpenBLAS simultaneously corrupted internal state. Fixed by replacing `rake_nltk` with YAKE (pure Python, zero native extensions, better keyword extraction benchmarks) and adding `asyncio.Lock` serialization around `score_candidates_async()` in `rerank/bm25.py`. (2) Added `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1` env safeguards in `server.py` `main()` to prevent BLAS thread-pool spawning.
- **Middleware `suggested_prompts` referenced non-existent names** (`evaluate_web_results`, `research_gap_analysis`). Changed all references to `research_methodology` in `middleware/query_guidance.py`.

### Changed — FastMCP 3.x best-practices audit remediation
- **Tool docstrings**: Added Google-style `Args:` sections to all 10 core tools (`web_search`, `get_content`, `batch_get_content`, `discover_links`, `gemini_search`, `grok_search`, `youtube_search`, `youtube_transcript`, `generate_sitemap`, `academic_search`). FastMCP auto-parses these for per-parameter descriptions in the JSON schema sent to clients.
- **Tool catalog**: Added `version="1.0"` and per-tool `timeout` fields to `ToolCatalogEntry`; extended `tool_kwargs()` to pass them through. Timeouts: sitemap 90s, grok/web_search/batch 60s, academic 45s, get_content 30s.
- **Server identity**: Added `version="0.1.8"` to `FastMCP()` constructor.
- **Resources**: Added `tags` and `annotations={"readOnlyHint": True}` to all 7 resource registrations; changed `analytics://reports/{report_name}` to `analytics://reports/{report_name}{?days}` so clients can discover and override the `days` parameter via RFC 6570 query-string syntax.
- **Prompts**: Added `version="1.0"` to all prompt registrations.
- **YouTube tools**: Added `ctx: Context = CurrentContext()` parameter and progress reporting to `youtube_search` and `youtube_transcript`.

### Added — Agent guidance surface
- **Server `instructions`**: Rewrote as the flagship web search methodology — covers decomposition, reconnaissance-first, iterative rounds, deep-reading, termination criteria, and tool routing chain. Follows the MCP blog's server-instructions design rules: captures cross-feature relationships, documents operational patterns, never repeats tool descriptions.
- **New `research_methodology` prompt**: Full methodology reference with decomposition strategy (worked example: "Should we adopt Rust?"), phase-by-phase guidance, gap analysis checklist, anti-patterns, and termination criteria. Registered with `version="1.0"`, tagged `{"research", "workflow"}`.
- **`docs://workflow` resource**: Refactored to a clean tool-routing reference card — lookup table of tool → key parameters, pagination patterns, summary modes, filter parameters, diagnostic resources. All philosophy/methodology moved to `instructions` and `research_methodology` prompt.
- **Dependency**: Replaced `rake-nltk>=1.0.6` with `yake>=0.4.8` in `pyproject.toml`. YAKE is pure Python (no NLTK/scipy dependency), has better benchmark scores across 20 datasets, supports deduplication natively, and eliminates the scipy thread-safety crash vector from keyword extraction.

### Added - Architecture documentation
- **`architecture.md`** (repo root) — comprehensive, source-verified system architecture derived from a GitNexus graph traversal of `web-search-mcp` (515 files, 7,322 nodes, 300 execution flows). Covers entry points (FastMCP server, `web-search-cli`, separate classifier service), the shared `execute_web_search` pipeline (six-branch plan → retrieve fanout → blocklist → provider-consensus RRF → BM25 → bi/cross/RankLLM rerank funnel), and every subsystem (content Tier1/Tier2, cache, embeddings, entity, Qdrant index, analytics/DuckDB, middleware, llm router, prompts, tools, telemetry, A/B, training, utils). Replaces the removed historical `docs/ARCHITECTURE.md` and records doc/impl discrepancies found during the mapping (Tavily-only sitemap with a stale Crawl4AI-fallback docstring; rerank `AGENTS.md` over-stating OpenRouter primacy for the RankLLM stage).

### Changed - quick_web_search backend (Composio/Tavily → Parallel AI)
- **Refactor**: Replaced Composio/Tavily backend with Parallel AI Search API (advanced mode, `parallel-web` SDK).
- **New file**: `src/kindly_web_search_mcp_server/quick_web_search.py` — self-contained module with models, impl, and MCP registration.
- **Inputs**: Required `search_queries` (1-5, 2-3 recommended keyword queries) and `objective`; optional `max_results`, `max_chars_total`, `max_chars_per_result`, `client_model`, `session_id`, `include_domains`, `exclude_domains`, `after_date`, `location`, `max_age_seconds` (min 600), `timeout_seconds`, `disable_cache_fallback`.
- **Outputs**: Response field `query` renamed to `search_queries`; removed always-None `answer` field. Added `search_id`, `session_id`, `warnings`, `usage` metadata; citations now include `publish_date` and `excerpts` list.
- **Config**: Added `PARALLEL_API_KEY` to settings; added `parallel-web>=1.0` dependency.
- **CLI**: `search quick` now requires repeatable `--search-query` (1-5) and `--objective`.
- **Removed**: `QuickWebSearchCitation`, `QuickWebSearchResponse`, `QuickWebSearchResultType` from `models.py` (now in `quick_web_search.py`).
- **Tests**: New `tests/test_quick_web_search.py` with focused Parallel search coverage; old Composio-backed tests removed from `test_composio_tools.py`.

### Fixed - Assessment cross-evaluation remediation (10 findings)
- **OTel stdout**: Phoenix initialization output redirected to stderr via `_redirect_stdout_to_stderr()` in `telemetry/init.py`, preserving clean JSON stdout for CLI commands.
- **Crawl4AI dict serialization**: `Crawl4AIClient.fetch_markdown` now extracts `markdown` field from dict responses instead of calling `str(dict)`.
- **latency-breakdown SQL**: Wrapped multi-leg `UNION ALL` in a subquery so DuckDB can resolve `ORDER BY CASE stage` binder.
- **Firecrawl dependency**: Confirmed `firecrawl-py` installed; added `try...except ImportError` guard in `get_firecrawl_client()` and `firecrawl_importable` doctor check.
- **SKILL.md drift**: Replaced invalid `provider_health` report name with `provider-performance`; removed stale `--num-results` from `search web`; updated `diagnostic` capability profile.

### Removed - MCP analytics tools (user directive)
- Deleted `analytics/tools.py` (orphan `analytics_query`/`analytics_report` MCP tool wrappers); removed from `TOOL_COVERAGE` and MCP tool column in SKILL.md. Native CLI `analytics query`/`analytics report` commands remain fully operational.

### Changed - Architecture
- **content/search decouple**: Moved `canonicalize_url` implementation to `utils/url_canonicalize.py`; content/ and tools/ now import from utils, not search/normalize.
- **telemetry init**: Added `shutdown_telemetry` to public re-exports; internal/init imports made explicit while public sub-modules keep star-export pattern (full explicit re-export deferred).

### Fixed - Tests
- Deleted `test_diversity_ranking.py` and `test_rerank_pipeline_eval.py` (imports removed `rerank.diversity`).
- Fixed `test_rerank_core.py` unused `DiversityStageOutcome` import.
- Fixed `test_public_output_serialization.py` syntax error (missing `from models import`).
- Updated 6 CLI test patches from stale `cli.commands.*` to `cli.services.*` targets.
- Fixed `test_experiments_create_requires_config` to tolerate OTel banner on stderr.
- Updated `test_reference_tools_covers_current_catalog` expected count (14→11).
- Aligned `test_brief_prints_one_paragraph` and `test_root_help_emits_structured_json` with current SKILL.md wording.

### Known Issues
- `scripts/rerank_eval_diversity.py` imports deleted `rerank.diversity` module; requires migration to current rerank API.
- `telemetry/__init__.py` retains wildcard imports for public modules; explicit re-export was deferred after restoring stable compatibility (no regressions, ruff clean).

### Changed - Content fetch reliability and efficiency
- Changed `batch_get_content` summaries to a single Gemini call fed all URLs via the URL-context tool, using `GEMINI_SECOND_API_KEY` for paid-tier rate limits; per-item fallback also uses the paid key.
- Added page-cache pre-check inside `run_batch_fetch` so `batch_get_content` reuses cached pages instead of re-fetching.
- Made `PageDuckDBCache`/`PageCache` lookups and stores async via `asyncio.to_thread`, with resilient fallbacks so cache errors never fail the tool.
- Added per-stage retry with exponential backoff for Jina Reader, local HTTP, and Crawl4AI; Crawl4AI respects `retryable=False`.
- Added per-stage timeout budgets in `fetch_content_artifact` so later stages get a fair share of the tool budget.
- Added a module-level circuit breaker for Jina Reader that opens after 3 failures in 60 seconds and falls through to downstream stages.
- Strengthened Camoufox cold-start retry from 1 attempt to 3 with exponential backoff (2s, 4s, 8s).
- Raised the `classify_markdown` success threshold from 30 to 80 words and added SPA shell detection.
- Added content-type validation in `safe_fetch_url` to reject non-HTML/XML/plain responses.
- Added `content_quality` and `content_word_count` to `GetContentResponse` and `BatchContentResult`.
- Added boilerplate stripping in `extract_content_as_markdown` and improved the regex fallback for `<a>`, `<code>`, `<pre>`, `<blockquote>`, and `<img>`.
- Optimized Jina Reader headers (`content/jina_reader.py`) to request `frontmatter` output, use the `research` preset, and drop embedded links/images noise via `X-Retain-Links: none` / `X-Retain-Images: none`.

### Fixed - Cold-start stdio timeout
- Root cause: lazy imports of `openai.resources.chat`, `nltk`, and `scipy` contended for the Python global import lock during the first tool call under stdio transport, blocking the anyio event loop and exceeding the 120s MCP tool timeout.
- Fixed by pre-importing `openai.resources.chat` in `llm/router.py`, moving `rake_nltk` import to module level in `search/keyword_extract.py`, and adding `_warm_heavy_imports()` in `server.py` called before `mcp.run()`.

### Fixed - Resource and prompt visibility after v3 migration
- `enable(only=True, components={"tool"})` in `apply_tool_profile` added a blanket `Visibility(False, match_all=True)` that disabled all component types, not just tools. Resources and prompts were hidden because they carry no profile tags.
- Fixed by adding `mcp.enable(components={"resource", "template", "prompt"})` after the tool-only allowlist.

### Changed - Conditional RankLLM reranking
- Reworked the normal search rerank path around full-pool Cohere `rerank-v4.0-fast`, strict RankLLM listwise permutations, OpenRouter-to-Gemini failover, and conditional MMR diversity. RankLLM now receives the normalized query only while the cross-encoder receives the research goal as a separate structured input.
- Added frozen calibration/evaluation tooling for cross-score thresholds, fusion, diversity, and pipeline replay, plus a 40-pair borderline fixture.

### Fixed - Live rerank execution
- Fixed harmful-query detection matching `rce` inside `primary-source`, bounded RankLLM with native async LiteLLM transport, accepted complete RankLLM sliding-window permutations, and suppressed RankLLM constructor prints that polluted JSON CLI stdout.
- Decoded RankLLM's YAML regex source literals with `ast.literal_eval` before strict validation; without this, complete live sliding-window responses were rejected and the pipeline fell back to Cohere.


### Changed - Direct OpenAI-compatible LLM clients
- Replaced LiteLLM with `openai.OpenAI` / `openai.AsyncOpenAI` across runtime and offline judge calls. Provider endpoints retain their configured `base_url`, timeout, and model, while client retries are disabled with `max_retries=0` so 429 `Retry-After: 60` responses cannot reintroduce the orchestration latency spike.
- Replaced LiteLLM OpenInference instrumentation with the OpenAI instrumentor and removed the direct LiteLLM dependency. The optional `mcpevals` extra still brings LiteLLM transitively through DSPy.
- Added `openai/gpt-oss-120b:nscale` through `huggingface_hub.InferenceClient` between Groq and Vercel in the worker ladder. The synchronous Hugging Face call is isolated with `asyncio.to_thread` and bounded by the same per-endpoint timeout/failover loop.

### Changed - Search retrieve budget
- Every planned provider is now attempted without runtime health/cooldown gating. One phase-level retrieve budget preserves completed results for ranking and records budget-exceeded provider tasks as `incomplete` with `error_type="retrieve_budget"`.

### Fixed — Analytics cutover to fixed six-branch model
- **Analytics DuckDB schema aligned to the fixed six-branch topology.** `search_branches` and `provider_calls` now use `branch_role` (not `branch_target`), `support_terms` (not `must_keep_terms`), and no `branch_weight`. `search_quality_scores` no longer has `rewrite_variant_count`; `branch_count` is computed from `search_branches`. Quality metrics query the live unified tables (`search_candidates`, not `merged_candidates`; no `query_rewrites`). Daily summaries read from `search_runs` and `provider_calls` with correct column names (`latency_ms`/`error_type`, not `duration_ms`/`error_code`); `summary_intent_daily` replaces `decomposition_rate`/`fallback_rate`/`avg_rewrite_variants` with `avg_branch_count` (observability invariant, expect 6.0). `summary_rerank_daily` normalizes NULL providers to `'internal'` with `COALESCE` and declares `provider NOT NULL` to match the composite PK. The `_migrate_rerank_stages()` compatibility path is removed; the analytics database is disposable and recreated from fresh DDL with no migration. `reports.candidate_survival` reads from `provider_calls.candidate_urls`, `search_candidates`, and `final_results` (not `merged_candidates`). `vw_branch_summary` includes `support_terms`. `vw_rerank_timeline` uses live rerank stage names. Stale `search_events`/`query_understanding`/`query_rewrites`/`provider_candidates`/`merged_candidates` references removed from analytics AGENTS guide and DuckDB schema docs.
- **Search quality persistence ordering fixed.** Quality metrics now run inside the same dedicated DuckDB worker callback after all unified fact rows have been inserted, eliminating the asynchronous read-before-write race that produced false zero candidate/final-result counts.
- **Shared Qdrant embedding dispatch fixed.** `search_qdrant` now accepts the six-branch service’s precomputed query embedding; the provider adapter no longer raises `TypeError` by injecting an unsupported keyword and no longer recomputes the same Hugging Face embedding.
- **Bright Data Yandex raw-response support added.** Yandex URLs follow the documented `text`/`lr`/`lang` contract without unsupported `brd_json=1`; raw organic result HTML is parsed while advertisement containers are excluded.
- **Bright Data retrieval timeout envelope aligned with provider HTTP budget.** `retrieval._call_provider` no longer wraps Bright Data adapters in a 10s catalog default while `run_provider` allows ~20s × 3 attempts plus backoff; outer timeout now derives from `BRIGHTDATA_GOOGLE_TIMEOUT_SECONDS` / `BRIGHTDATA_BING_TIMEOUT_SECONDS`. Removed redundant `asyncio.wait_for` around Bing sidecar HTTP (httpx timeout only).
### Fixed — Query embedding dropped from analytics when rerank produced a context
- **Query embedding dropped from analytics when rerank produced a context.**
  `ranking.py` now copies `RerankEmbeddingContext.query_embedding` onto
  `DiagnosticsCollector.query_embedding` alongside the candidate copy, so
  `query_embedding_dim` is no longer null and `query_embeddings` persistence
  is no longer skipped. Add a regression test
  (`tests/test_query_embedding_propagation.py`) covering the collector state,
  the `build_diagnostics` projection, and the `persist_search_outcome` write
  dispatcher.

### Fixed — Retrieval-budget and caller-cancellation cleanup latency

- **Retrieval-budget and caller-cancellation cleanup latency fixed.**
  Retrieval-budget and caller-cancellation cleanup no longer wait indefinitely for
  provider task unwinding. Bounded cancellation drain is extracted into a reusable
  utility `cancel_and_drain_tasks` and applied at both cleanup sites in `retrieval.py`
  to respect search retrieve budget and client request deadlines.

### Removed
- Perplexity search surface removed entirely: `PerplexitySearchResponse` model and `PerplexitySearchResultType` alias from `models.py`, `perplexity_search` from `EXPENSIVE_TOOLS` in `rate_limits.py`, "Perplexity Sonar" steering message replaced with generic expensive-tool guidance in `expensive_tool_protection.py`.
- Perplexity telemetry removed: `record_perplexity_search`, `get_perplexity_metrics`, `PERPLEXITY_DEPTH`/`PERPLEXITY_SOURCE_COUNT`/`PERPLEXITY_MODEL` constants and their re-exports from `telemetry/__init__.py`, `attributes.py`, `metrics.py`, `records_ai.py`.
- `POLLINATIONS_API_KEY` removed from environment docs in `CLAUDE.md`, `README.md`, and `skills/web-search-cli/SKILL.md`.
- `skills/web-search-cli/SKILL.md` fully refreshed to match current CLI shape: added `--debug` global flag, `sitemap generate`, `experiments` group; removed `agent research`; updated `search web` to require `--research-goal`, default `--num-results` 15 (clamped 15–50), added `--diagnostics`, removed `--provider`; added `--summary-mode`/`--focus-query` to `content batch`; added `--backend` to `youtube transcript`.
### Added
- Added `scripts/live_web_search_quality.py` and a fixed 50-query corpus for a resumable FastMCP stdio quality campaign: ten batches of five concurrent rewrite-enabled searches, a first-batch DuckDB/debug-log gate, exact-attempt accounting, raw MCP/progress capture, structured analytics exports, aggregate quality metrics, and deterministic manual-review artifacts.
- Added pandas 3.x/pyarrow exports for campaign calls, progress, per-query quality, manual review, process logs, and analytics tables. Each `pandas/*.parquet` file uses Zstandard compression, omits the DataFrame index, JSON-encodes nested object values, and is read back to verify its row count.
- DeGoog search aggregator as free provider alongside SearXNG
- Brave LLM Context replaces the standard Brave web path in `search_brave()` (`/res/v1/llm/context`, `grounding.generic` → `WebSearchResult`).
- New `brave_news` specialized provider for the `news` intent (`/res/v1/news/search`, `page_age` → `published_date`).
- `brave_common.py` centralizes Brave API key, headers, query bounds, and freshness translation across Brave surfaces.
- `BRAVE_GOGGLES_BY_INTENT` settings field (default `{}`) merges intent-configured Goggles into `brave` / `brave_news` provider arguments.
- `ProviderExecutionPlan.specialized_provider_names` and a `specialized_original` branch wire intent-policy specialized providers (e.g. `telegram`, `brave_news`).
- `web-search-cli --debug` to enable DEBUG-level application logging on stderr while keeping command JSON on stdout.
- Strict `WebSearchRequest`/`QueryBranch` contracts, immutable 19-provider metadata registry, `bm25s` lexical scoring, and detached search-outcome lifecycle.
- Camoufox stealth-Firefox sidecar as last-resort browser fallback.
- `CamoufoxClient` / `CamoufoxClientError` in `remote_clients.py` with 503 retry, 8 MiB cap, health cache.
- `_fetch_via_camoufox` stage in `stages.py` (raw HTML -> markdown + metadata + links).
- `specialized_pipeline.py` module extracted for Tier-1 resolver orchestration.
- `CAMOUFOX_BASE_URL`, `CAMOUFOX_TIMEOUT_SECONDS`, `CAMOUFOX_HEALTH_CACHE_SECONDS` settings.
- `CONTENT_STAGE_CAMOUFOX` telemetry attribute.

### Breaking changes
- **2026-07-12 — Shared web-search service cutover.** MCP and CLI now construct the same validated request and call `execute_web_search`; `research_goal` is required and `num_results` accepts only 15–50. Explicit `rewrite=False` retains deterministic keyword/Autosuggest/Spellcheck enrichment instead of literal-syntax auto-bypass.
- **2026-07-10 — Query rewrite and reranking overhaul.** This is a clean break: `original_free` routes the original query to `free` providers, `keyword_refined` routes to keyword/SERP providers, and `neural_refined` routes to neural providers. Literal search syntax bypasses the LLM rewrite. RAKE-NLTK extracts ranked `must_keep_terms` from `research_goal`; Brave Autosuggest uses `rich=true` and the separate `BRAVE_SUGGEST_API_KEY`, while spellcheck uses `BRAVE_API_KEY`. Branch results are filtered through the DuckDB-backed URL blocklist before merge. Merge is pure rank-based RRF with per-intent `rrf_k` and no provider/list weights. The Qwen XML listwise-CoT reranker now escapes untrusted candidate fields, shuffles display IDs and remaps them, parses only `<final_ranking>`, and assigns normalized linear ordinal scores. LLM output is accepted only when error-free with non-empty relevance scores. Bi-encoder and cross-encoder stage multipliers form a monotonic funnel, and diversity is terminal with no tail reattachment.

### Removed
- Removed the experimental LangChain/LangGraph agentic research stack, its `agent` CLI command, tool registration, telemetry, settings, dependencies, and dedicated tests.
- Local `crawl4ai` Python package + transitive `playwright`/`playwright-stealth` deps.
- `legacy_sitemap.py` (Crawl4AI deep-crawl sitemap fallback).
- `CONTENT_STAGE_NODRIVER` telemetry attribute.
- `BROWSER_EXECUTABLE_PATH` env var from README.

### Changed
- Tier-2 order: Jina Reader -> Crawl4AI /md -> local BS4 (conditional) -> Camoufox last-resort.
- `crawl4ai_client.py` renamed to `remote_clients.py`; `Crawl4AIClient.crawl()`/`deep_crawl()` removed.
- `fallback.py` renamed to `stages.py`; `fallback_fetch_content` wrapper removed.
- `fetch_pipeline.py` rewritten to delegate stages to `stages.py` and `specialized_pipeline.py`.
- `batch_orchestrator.py` simplified to per-URL `fetch_content_artifact` only.
- `sitemap.py` simplified to Tavily-only; legacy fallback deleted.
- 6 specialized resolvers moved to `content/resolvers/` subfolder.
- **Phoenix tracing lifecycle** now uses `phoenix.otel.register` with the `WebSearchMCP` project and local SSH-forward endpoint `http://127.0.0.1:6006/v1/traces`; LiteLLM, LangChain, and HTTPX instrumentation share one provider and shutdown follows outcome drain → persistence → HTTP → telemetry.
- **VPS service endpoints corrected** — `.env` now targets SearXNG at `127.0.0.1:8080`, DeGoog at `127.0.0.1:4444`, and Phoenix OTLP HTTP at `127.0.0.1:6006/v1/traces`, matching the SSH-forwarded VPS services; Hermes keepalive job `7acbeb2b3573` now specifies the complete manifest forward list.
- **Crawl4AI remote endpoint enabled** — `.env` now points `CRAWL4AI_BASE_URL` to the manifest-mapped SSH forward `http://127.0.0.1:11235`.
- **Phase Two — Brave retrieval:** `news` intent policy version `1.1` adds `brave_news` via `specialized_original`; BrightData news URLs map freshness to `tbs=qdr:`; cache identity fingerprints per-provider arguments; DDGS documented as peer `free` provider (not fallback).
- **Modularized telemetry package** — split `src/kindly_web_search_mcp_server/telemetry.py` into a focused `telemetry/` package (`attributes.py`, `constants.py`, `init.py`, `metrics.py`, `spans.py`, `span_enhancements.py`, `records_*.py`, `_internal.py`). The public API is preserved via `telemetry/__init__.py` re-exports; all existing imports from `.telemetry` continue to work.
- **Rerank bi-encoder hot path repaired** — the HF bi-encoder now runs for normal overfetch windows by default, embeds bounded title/snippet candidate text (`RERANK_BI_ENCODER_TEXT_MAX_CHARS=384`), keeps normal windows in one batch (`RERANK_BI_ENCODER_BATCH_SIZE=64`), and uses a single latency-sensitive candidate-embedding attempt (`RERANK_BI_ENCODER_TIMEOUT_SECONDS=15.0`). The per-call `AsyncInferenceClient` singleton is reused for connection pooling; concurrency is controlled by the per-caller wrappers (Qdrant `BatchLimitedEmbeddings`, bi-encoder batch semaphore) rather than a process-global gate.
- **Rerank candidate analytics batched per stage** — candidate-survival rows are now inserted with one DuckDB connection/executemany per stage instead of per-candidate writes inside the awaited rerank path.
- **SerpApi default engine switched to Yahoo** — the provider now defaults `SERPAPI_DEFAULT_ENGINE` to `yahoo`, keeping multi-engine support intact while broadening the default non-Google coverage.
- **Grafana dashboards aligned to current telemetry** — the pipeline dashboard now uses `web_search_rrf_provider_contribution`, the content dashboard now tracks `crawl4ai_remote`, and the providers dashboard now includes circuit-state visibility. `grafana/README.md` and the Grafana dashboard regression tests were updated to match.
- **HF Inference API connection reuse** — `embed_texts` now uses a singleton `AsyncInferenceClient` instead of creating a new instance per call. The HF library lazily creates an internal `httpx.AsyncClient`; reusing the same instance gives TCP/TLS connection pooling. Latency dropped from 5-6s to ~1s per embedding call (~5x improvement).
- **Reranking pipeline overhaul** — MMR now uses reranker scores for relevance instead of embedding cosine similarity. The cross-encoder/LLM reranker scores are min-max normalized and used as the MMR relevance term; embeddings are only used for the diversity (document-to-document) term. This fixes the critical issue where MMR ignored expensive reranker scores and recomputed relevance from weaker embedding similarity.
- **MMR lambda default changed from 0.5 to 0.7** — relevance-weighted for web search (was 50/50 relevance/diversity, now 70/30). Research consensus: λ=0.7-0.8 for precision search.
- **Cross-encoder document construction enriched** — now includes Domain, Providers, ProviderCount in addition to Title, Snippet, URL. Snippet moved to second position (after Title) for better semantic importance with Cohere rerank v4.
- **Bi-encoder stage target is now configurable** via `RERANK_BI_ENCODER_STAGE_MULTIPLIER` (default `3.0`), preserving a wider shortlist for the cross-encoder without hardcoding a second candidate limit.
- **LLM reranker prompt uses the Qwen template's query and research_goal fields** — candidate payload remains limited to escaped title, URL, and snippet.
- **LLM reranker scores use normalized linear ordinal scoring** — the first ranked candidate scores `1.0`, the last scores `0.0` (or `1.0` for a one-candidate list), and scores are remapped after display-ID shuffling.
- **Terminal diversity ordering** — MMR consumes min-max-normalized reranker relevance scores for relevance and embeddings for the diversity signal, then returns only the diversified top-k slice without reattaching a tail.
- **Reranker fallback chain simplified to ONE chain**: `cohere_fast -> cohere_fast_openrouter -> voyage`. Always tries in this order regardless of configured engine; the existing Jina and GCP Cloud Run adapter modules remain available for direct integrations.
- **Modularized `server.py` tool handlers** — split `@mcp.tool` handlers, resources, and prompts into focused modules under `src/kindly_web_search_mcp_server/tools/`. `server.py` is now a thin registry that imports and registers handlers on the `mcp` instance. Fixed the missing `num_results` parameter in `web_search` and replaced logging f-strings with lazy formatting in touched code.

### Added
- Added `plans/grafana-observability-refresh-plan-2026-07-03.md` to reconcile the live Grafana setup with the current app telemetry, including the crawl4ai content stage, provider health visibility, branch/result lineage, and MotherDuck-backed quality panels.
- Added `plans/web_search-latency-report-2026-06-30.md` documenting the live MCP timeout analysis, provider bottlenecks, and code-path latency sources for `web_search`.
- Corrected `plans/web_search-latency-report-2026-06-30.md` to reflect the actual root causes: missing outer timeout, repeated provider bundle across branches, and the shared paid-provider semaphore bug.
- Added `plans/provider-root-cause-remediation-plan-2026-06-29.md` documenting the provider latency/root-cause findings and the no-new-tests remediation plan.
- Reworked `plans/IN-DESIGN/observability/full-pipeline-observability-implementation-plan-2026-06-30.md` into a live DuckDB-backed clean-break observability design for full `web_search` coverage, including branch/provider/rerank lineage and exact returned-response analytics.
- Added the DuckDB observability implementation for full `web_search` coverage: tool-call rows, returned-response rows, branch attempts, branch candidates, provider health transitions, and pipeline heartbeats, plus returned-object views and candidate-survival analytics.
- Added the repo-doc consolidation note and simplified `CLAUDE.md` to point at `AGENTS.md` as the single workspace guidance source.

### Fixed
- **MCP startup import regression** — deferred the experimental agent runner and RAKE-NLTK imports until their respective tools execute, removing LangChain/LangGraph and NLTK from the standard stdio startup path.
- **LiteLLM route model IDs separated from reported provider model IDs** — Cerebras/Groq worker calls now send provider-qualified route models such as `cerebras/gpt-oss-120b` and `groq/openai/gpt-oss-120b` to LiteLLM while preserving raw provider model IDs in telemetry.
- **Query rewrite model IDs now match the documented providers** — the rewrite/classifier LLM router now uses the documented Cerebras, Groq, and Vercel model IDs directly instead of inventing nested provider-prefixed strings. This restores the intended Cerebras → Groq → Vercel fallback ladder and avoids the malformed Vercel rewrite default.
- **Async DuckDB analytics no longer blocks the event loop** — the hot observability and pipeline write paths now dispatch DuckDB inserts through a shared background-write helper, covering search events, provider calls/candidates, rerank stages, final results, search runs, and pipeline observability inserts.
- **HF Inference API connection reuse** — singleton `AsyncInferenceClient` eliminates per-call TCP/TLS handshake overhead. Embedding latency dropped from 5-6s to ~1s (~5x improvement).
- **Search import path cleaned up** — removed stale `task_scope` references from live provider code and tests, kept the branch executor on direct `asyncio` primitives, and made BrightData Bing cancellation re-raise instead of returning an empty result list.
- **DuckDB now logs which reranker was actually used** — `search_runs` table has new `reranker_provider` and `reranker_model` columns. `RerankOutput` carries `provider`/`model` through the pipeline so the final chosen reranker (including fallback winners like `voyage` or `groq`) is recorded per run.
- **Search provider connect timeouts fixed** — `base_provider.py` now uses `httpx.Timeout(connect=5.0, read=25.0)` instead of a single 30s total timeout. Dead/unreachable providers (like `search_router`) fail fast at ~5s instead of hanging for 54s on TCP SYN retransmissions.
- **DuckDuckGo provider timeout now configurable** — `ddg_timeout_seconds` setting (env: `DDG_TIMEOUT_SECONDS`, default `10`) controls `DDGS(timeout=...)`. Previously hardcoded to 10s.
- Disabled the Google CSE provider registration so the search stack no longer routes live traffic through a Google Custom Search path that is blocked for this project.
- Redacted Google CSE 403s so `API_KEY_SERVICE_BLOCKED` now reports a clear Google Cloud authorization message instead of leaking the raw request URL.
- Switched both the Composio `web_search` provider path and `quick_web_search` to execute `COMPOSIO_SEARCH_TAVILY`, the live-working Composio Search action, and updated quick-search parsing for Tavily's `answer` plus `results` response shape.
- Updated the default Composio Search toolkit version to `20260618_00`; live probes showed the prior `20260424_00` pin returned a misleading `COMPOSIO_EXA_API_KEY` backend error for Composio Search actions.
- Bounded and cached the Qdrant query-embedding path so decomposed search branch fanout no longer stampedes Hugging Face inference with one raw embedding call per Qdrant branch.
- Hardcoded the Google CSE engine ID to the live configured value `771d303cf528e4b7c` so the Google CSE provider no longer depends on an unset `GOOGLE_CSE_ENGINE_ID`.
- Switched the Composio search provider parser to accept both citation-shaped and raw-result-shaped Composio payloads.
- Made the Composio client read `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` from the live environment at call time so quick search follows the same credentials loaded by the server bootstrap.
- Added branch-executor headroom so decomposed search branches can return provider partials before the wrapper cancels the task.
- **Fixed provider duplication in branch planner** — `_shard_providers` no longer pads provider lists with `cycle()`, so each provider appears in exactly one branch instead of being invoked multiple times per branch.
- **Fixed redundant branch creation when rewrite is disabled** — `pipeline.py` no longer injects an extra `QueryVariant` when `rewrite=False`; the canonical original branch from `build_search_branch_specs` is the only branch.
- **BrightData Google search now respects a configurable timeout** — new `BRIGHTDATA_GOOGLE_TIMEOUT_SECONDS` setting (default `20.0`) used as both the per-request and `run_provider` timeout. Google and Bing requests are now concurrent instead of sequential.
- **BrightData per-attempt logging** — logs each Google/Bing attempt URL and timing for easier observability.

### Added
- Unit tests for branch planner: provider sharding without duplication and correct branch counts for rewrite enabled/disabled (`tests/test_branch_planner.py`).

### Changed — Code review safe-refactors (#2 #4 #5 #6 #9 #10)

- **`#2` Dead code (`analytics/writers/connection.py`, `analytics/writers/schema.py`):** Removed empty `if TYPE_CHECKING: pass` no-op blocks and the now-unused `from typing import TYPE_CHECKING` imports. No semantic change.
- **`#4` Redundant `dc.merged_candidates` per-item copy (`search/ranking.py`):** Replaced `[result.model_copy() for result in merged]` with `list(merged)`. The downstream rerank call already buffers its input through a per-item `model_copy`, so the analytics snapshot is already isolated. Kept the rerank-side copy because `apply_entity_overlap_boost` writes `candidate.score` directly on the model instance, which would otherwise leak into the analytics snapshot.
- **`#5` Redundant `canonicalize_url` calls (`search/merge.py`, `search/ranking.py`):** Added `_memoize_canonicalize` helper in `merge.py`; `reciprocal_rank_fusion` accepts an optional `canonicalize: Callable[[str], str]` kwarg and uses the supplied callable directly when given, or wraps `canonicalize_url` internally when `None`. `merge_search_results` and `rank_and_finalize` share one memoizing wrapper per call so each distinct raw URL is canonicalized at most once across the overlap counter, two RRF invocations, and the per-result `url_key` lookup. Net reduction: up to 5× per distinct URL per `rank_and_finalize` call.
- **`#6` Redundant `len(markdown.split())` calls (`content/stages.py`):** Hoisted the value into a `word_count` local in `_fetch_via_jina`, `_fetch_via_crawl4ai`, and `_fetch_via_camoufox`. Three functions, two `word_count=` sites each (one in `record_content_resolution`, one in `ContentArtifact`). `_fetch_via_local` was not touched because its two `markdown.split()` calls live in different branches (PDF vs HTML) and never both run for the same `markdown` value.
- **`#9` Bare `except Exception: pass` (`cache/page_duckdb.py`):** Narrowed the index-creation catch to `duckdb.Error` and added `logger.warning(...)` so disk/permission problems surface in the logs while genuine concurrent-create races remain tolerated. Added a module-level `logger`.
- **`#10` Six-times-duplicated `QueryBranch(why=...)` conditional (`search/planning.py`):** Extracted the `use_llm_why` boolean and a `_why_for(role, llm_label)` helper backed by a small `_DETERMINISTIC_WHY` dict. All five paid/neural/specialized branches now share one selector. The first branch (ORIGINAL_FREE) still uses the unconditional `"original normalized query"` string.

### Added — Regression tests for the above

- `tests/test_search_ranking.py` — `rank_and_finalize` rerank isolation (#4) and `canonicalize_url` call-count proof (#5).
- `tests/test_search_merge_cache.py` — `_memoize_canonicalize` behavior across the default-None path, the supplied-callable path, and `merge_search_results` shared-cache path (#5).
- `tests/test_page_duckdb_schema_errors.py` — `duckdb.Error` → warning logged, non-duckdb exception → propagates (#9).
- `tests/test_search_planning_why.py` — `use_llm_why` boolean contract under 4 rewrite scenarios (#10).

### Skipped (origin `e07ca83` already covered)

- `#3` `utils/environment.py` consolidates 4 copies of `_get_int_env`/`_get_float_env` on origin; arxiv now delegates to it. The review's premise that there is a `utils/environment.py` to import from was correct post-pull.
- `#7`, `#8` origin's `e07ca83` commit message explicitly says "clean up hot-path imports" and the `anchor_today` lazy import is no longer in `content/summary_backend.py`. Verified by reading the post-pull source.

### Verification

- `ruff check` clean on all 7 production files touched + 4 new test files.
- `python -m py_compile` clean on all touched files.
- 23/23 focused tests pass (pytest via `uv run`).
- 3 pre-existing test failures in `test_rerank_pipeline_integration.py` confirmed unchanged from clean `HEAD` (signature drift unrelated to this refactor).

## [0.4.0] — 2026-06-28

### Added
- **TinyBERT-4L ONNX INT8 intent classifier** — replaces LLM-backed query understanding as primary path. 83% accuracy, 84% F1 macro across 6 search intents. ~5ms latency vs ~60s LLM.
- **6-class SearchIntent system** — expanded from 4 intents (`general`, `ai_coding`, `digital_humanities`, `comparison`) to 6 (`general`, `ai_coding_and_infrastructure`, `digital_humanities`, `comparison`, `social_media`, `news`). Updated all intents.py, intent_policy.py, schema, prompts, analytics judges.
- **Dockerized classifier service** on VPS at port 8686. FastAPI with /health and /classify endpoints. CPU-only torch image, 300MB RAM, auto-restart.
- **Persistent SSH tunnel** via systemd user service + autossh (port 18686 → VPS:8686). Auto-restarts on connection drop.
- **Training pipeline** — distilabel + Gemini API for synthetic data generation, custom GeminiLLM class, class-weighted WeightedTrainer, ONNX export + INT8 quantization.
- Classification report: general=0.86, social_media=0.87, digital_humanities=1.00, comparison=0.76, ai_coding=0.79, news=0.73.

### Changed
- `resolve_query_understanding` in resolver.py — ONNX classifier is primary path, LLM query understanding is fallback (only when classifier service is down).
- Added `intent_classifier_url`, `intent_classifier_timeout_seconds`, `intent_classifier_confidence_threshold`, `intent_classifier_enabled` settings.
- Classification report: general=0.86, social_media=0.87, digital_humanities=1.00, comparison=0.76, ai_coding=0.79, news=0.73.
- Created `docs/ARCHITECTURE.md` and `docs/ARCHITECTURE-DIAGRAMS.md` documenting the system architecture and data flows.
- Added `docs/CONFIGURATION.md` with environment variables and setup guide.
- Added `docs/GETTING-STARTED.md` quick start guide.
- Added `docs/DEVELOPMENT.md` and `docs/TESTING.md` development patterns and workflows.
- Added `docs/CONTRIBUTING.md` contribution guidelines.
- Added `plans/` directory with initial roadmap and design documents.
- Added `tests/test_branch_planner.py` for branch planner tests.

### Fixed
- Fixed classifier service URL to use the live VPS endpoint.
- Resolved query understanding fallback to LLM when classifier service is down.
- Fixed intent classification confidence threshold handling.

## [0.3.0] — 2026-06-15

### Added
- Initial web search MCP server with multi-provider search (SearXNG, Tavily, Brave, Jina).
- RRF merge and reranking pipeline.
- Content extraction pipeline with 7-stage resolution.
- YouTube transcript and search tools.
- Academic search across 6 scholarly sources.
- Gemini grounded search and Grok search.
- Semantic sitemap generation.
- Query cache and page cache layers.
- OpenTelemetry and DuckDB observability.

### Changed
- N/A

### Fixed
- N/A

## [0.2.0] — 2026-06-01

### Added
- Prototype MCP server with basic web search.

### Changed
- N/A

### Fixed
- N/A

## [0.1.0] — 2026-05-15

### Added
- Initial project scaffolding.
