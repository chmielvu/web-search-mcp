# AGENTS.md - Analytics & Search Quality

## FlockMTL automatic judgment pipeline (since 2026-07-20)

- **Orchestrator**: `analytics/judges.py::judge_search_run(run_key)` + `schedule_judge_search_run(run_key)`.
- **Persisted verdicts**: `llm_judgments` table (one row per LLM call). Columns: `recorded_at, run_key, judgment_kind, judgment_target, prompt_name, model_name, verdict, input_tokens, output_tokens, duration_ms, status, error_message, payload_json`.
- **Three judgment kinds** (row-per-unit, scalar verdicts):
  - `classify_failure` — once per failed/empty search run; `judgment_target` = run_key
  - `grade_relevance` — once per final result; `judgment_target` = result link
  - `judge_rewrite` — once per planner rewrite variant; `judgment_target` = `rewrite:0..3`
- **Trigger**: `schedule_judge_search_run` is fire-and-forget on a `ThreadPoolExecutor(max_workers=4)`, wired into `search/outcomes.py::submit_search_outcome`. Never blocks the user-facing search response.
- **Cost guard**: `settings.flockmtl_enabled` (default `true`); setting it false short-circuits the orchestrator before opening a connection.
- **Catalog**: `flockmtl_resources` table tracks registered FlockMTL resources (no built-in `duckdb_models()`/`duckdb_prompts()` introspection). Backs `vw_flockmtl_resources`.
- **Safe views**: `vw_llm_judgments` (read-only mirror, no per-row `llm_complete`) and `vw_flockmtl_resources` (catalog introspection).
- **Per-connection secret**: `_ensure_flockmtl_secret(connection)` re-registers `__default_openai` on every fresh connection (Option C — `CREATE SECRET` is connection-local; Option A PERSISTENT was rejected because it writes unencrypted key material to `~/.duckdb/secrets/`).
- **Mock judge**: `scripts/mock_judge_server.py` — OpenAI-compatible, returns `{"items": [{"verdict": "..."}]}` per flock's `ExtractCompletionOutput` JSON parse requirement.

This directory implements the DuckDB-backed analytics and evaluation layer.

## Current Structure

analytics/
|-- duckdb_store.py          # Thin facade re-exporting writers + schema
|-- writers/                 # Schema, inserts, core TableWriter, connection
|   |-- schema.py            # 9 fact/embedding tables + provider_health + quality + judge
|   |-- inserts.py           # Column lists + TableWriter instances for every table
|   |-- core.py              # TableWriter + public insert wrappers
|   |-- connection.py        # _db_path + _LOCK
|   |-- table_names.py      # Canonical table-name constants
|   |-- summary_schema.py   # 4 daily summary tables
|   |-- ab_schema.py        # A/B experiment/variant/assignment/result/shadow tables
|   `-- migrations.py       # Legacy search_events backfill (no longer used)
|-- async_writes.py          # Dedicated single-worker DuckDB write executor
|-- rerank_candidate_writes.py # Batched rerank candidate survival inserts
|-- observability_schema.py  # provider_health_transitions shim
|-- observability_store.py   # _candidate_id / _canonical_result_id helpers
|-- views.py                 # 13 dashboard views + A/B views + eval views
|-- queries.py               # Query helpers
|-- local_queries.py         # Local DuckDB query shortcuts
|-- reports.py               # Named analytics reports
|-- quality_metrics.py       # Run-level quality scoring
|-- summaries.py             # Daily aggregate refresh
|-- judge_prompt.py          # Judge prompt construction
|-- judge_runner.py          # Fire-and-forget judge evaluation
|-- judge_calibration.py     # Judge score normalization
|-- search_relevance_judge.py # 4D relevance-judge helpers
|-- evals.py                 # Evaluation helpers
|-- tools.py                 # Analytics utility helpers
|-- motherduck_sync.py       # MotherDuck sync helpers
|-- descriptions.py          # UI descriptions for tables/views
|-- app.py                   # Analytics UI app
|-- app_queries.py           # UI query helpers
|-- ui.py                    # UI rendering
|-- tabs/                    # UI tab modules
`-- formatting.py            # Formatting helpers

## Data Flow

All analytics rows join on `run_key`.

1. `search_runs` captures the request side (query, intent, confidence,
   rewrite metadata, branch count, provider count, result counts, phase
   timings)
2. `search_branches` captures one row per branch per run with
   `branch_role`, `support_terms`, `assigned_providers`,
   `attempted_providers`, and `results_count`
3. `provider_calls` captures every outbound provider call with
   `branch_role`, status, latency, and candidate URLs
4. `search_candidates` captures deduplicated RRF-scored candidates
   with provider overlap provenance
5. `rerank_stages` and `rerank_candidates` capture reranking; candidate
   survival rows are batched per stage so analytics does not add per-row
   DuckDB connection overhead to the rerank hot path
   The batched writer column list must match `writers/schema.py` and
   `writers/inserts.py`, including `candidate_id`, BM25/dense ranks and scores,
   raw cross/LLM scores, fused/hybrid scores, and diversity fields.
6. `final_results` captures the public output with provider provenance
8. `query_embeddings` and `candidate_embeddings` store vectors for
   vss similarity search
9. `llm_call_log` stores unified cost tracking across all LLM calls
   (rewrite, rerank, judge, embedding) keyed by `run_key`
10. `search_quality_scores` stores computed quality metrics
11. `judge_evaluations` stores asynchronous 4D judge results

## Branch-Role Model

The fixed six-branch topology stores `branch_role` (not `branch_target`)
on `search_branches` and `provider_calls`. The six roles are:

- `original_free`
- `paid_brave`
- `paid_google`
- `paid_other`
- `neural`
- `specialized`

Each branch owns its `provider_names` tuple explicitly; there is no
plan-level selected-provider list. `support_terms` replaces the old
`must_keep_terms`. Branch weights are removed.

`search_runs.branch_count` should normally be 6 (the fixed topology).
`summary_intent_daily.avg_branch_count` is the daily per-intent average
and serves as an observability invariant.

## Current Behavior

- DuckDB is the source of truth for the analytics layer.
- All persistence is non-blocking via `dispatch_duckdb_write` (single-worker
  executor). Hot-path collection is in-memory only.
- Views exist for both human-friendly queries and programmatic reporting.
- Judge evaluation is fire-and-forget and should not block the response path.
- Report and query helpers should stay aligned with the underlying schema.
- No migration or compatibility layer is needed; the analytics database is
  disposable and recreated from fresh DDL.
- `llm_call_log` is the unified source for per-call LLM cost attribution.
  Token/cost data also exists in `search_runs`, `rerank_stages`, and
  `judge_evaluations`; prefer `llm_call_log` for cross-purpose rollups.

## New WS4 Views

- `vw_end_to_end_quality` — denormalized join across all 7 pipeline grains
  plus quality and judge scores
- `vw_cost_attribution` — per-run, per-purpose cost rollups from
  `llm_call_log`
- `vw_embedding_similarity` — `array_cosine_distance()` between query and
  candidate embeddings

## Testing

- `python -m pytest tests/test_analytics_*.py`
- `python -m pytest tests/test_pipeline_tables.py tests/test_search_quality_scores.py`
