# DuckDB Analytics Schema — Recommendations Report

**Date:** 2026-08-12
**DB file:** `duckdb_data/analytics/search_events.duckdb` (181 MB, ~86k rows, 35 base tables, 32 views)
**DuckDB version:** v1.5.2 (Variegata)
**Scope:** Tables, views, indexes, and the join graph that supports the MCP's 12 published tools + 7 analytics resources.

---

## TL;DR — Top 12 things to fix (priority ordered)

1. **Repair the `tool_calls` ↔ `search_runs` join.** `tool_calls` has no `run_key`, and `search_runs.tool_call_id` is 99.6% NULL. Today you cannot answer "what was the cost of the rewrite that produced run X?" or "did run X hit any provider errors?". Add `tool_calls.run_key` (backfill from `event_id`/in-process correlation) or thread `run_key` through the writer.
2. **Populate `summary_*_daily` (or drop them).** All four `summary_intent_daily`, `summary_provider_daily`, `summary_quality_daily`, `summary_rerank_daily` are 0-row. `summaries.py` exists but no scheduler invokes it. Either wire a refresh job (cron / `apscheduler` / on-write trigger) or remove the DDL.
3. **Drop `_hnsw_test` (development leak).** 1-row table created in dev; should never have shipped.
4. **Drop `brave_spellcheck` (100% NULL, never populated).** Dead column on `search_runs`.
5. **Decide on the dead `rerank_candidates` columns.** 8 of 23 columns (`bm25_*`, `dense_*`, `fused_score`, `hybrid_rrf_score`, `recency_boost`, `entity_overlap_score`) are 100% NULL — the hybrid-rerank scoring fields the table was sized for are never written. Either start populating them, or shrink the table to what the pipeline actually emits.
6. **Decide on the dead `rerank_stages` config columns.** `score_threshold`, `alpha_blend`, `instruction_present/length`, `query_type_hint`, `entity_overlap_enabled` are 100% NULL — these are the knobs the rerank engine supposedly exposes per stage.
7. **Decide on the A/B testing subsystem.** All 5 `ab_*` tables are 0-row. Either commit to wiring `ab_*_results` writes from search outcomes, or drop the DDL and the 3 `v_ab_*` views to remove dead code from the public schema.
8. **Decide on the judge-evaluations layer.** `judge_evaluations` has 6 rows (legacy). The active path is `llm_judgments` (2,331 rows). `judge_evaluations`, `judge_rubrics`, `judge_calibration_set` are either legacy or never populated — collapse to one path.
9. **Decide on the eval_* tables.** 8 `eval_*` tables, 0 rows. These exist only for the `vw_eval_*` family of views. Either the eval harness is not used in production or the data path is broken.
10. **Fix `provider_calls` typed-field population.** `request_url`, `http_status`, `request_query`, `result_class`, `response_meta_json` are 96%, 96%, 63%, 63%, 63% NULL — these are the fields that let you diagnose provider failures at the URL level.
11. **Fix `tool_calls` model/provider/tokens/duration.** 95–100% NULL. Today the resource `analytics://reports/{name}{?days}` cannot compute a real "cost by model" because no `tool_calls.input_tokens` is set.
12. **Stop over-indexing HNSW.** There are two HNSW indexes on `query_embeddings.embedding` (`_t_idx2` auto-generated + `idx_qemb_hnsw`). Drop the auto-generated one. Same audit on `candidate_embeddings`.

---

## 1. Current state — what the DB actually contains

### 1.1 Inventory by row count

| Table | Rows | Comment |
|---|---:|---|
| `rerank_candidates` | 32,413 | Largest fact table |
| `search_candidates` | 31,078 | |
| `final_results` | 7,090 | |
| `provider_calls` | 6,243 | |
| `search_branches` | 2,972 | |
| `llm_judgments` | 2,331 | 8 judgment kinds observed |
| `tool_calls` | 1,417 | No `run_key` column |
| `rerank_stages` | 1,404 | |
| `search_runs` | 502 | |
| `search_quality_scores` | 318 | |
| `llm_call_log` | 281 | |
| `query_embeddings` | 257 | |
| `candidate_embeddings` | 80 | |
| `flockmtl_resources` | 11 | Metadata, expected low |
| `query_understanding_events` | 7 | |
| `judge_evaluations` | 6 | Legacy |
| `_hnsw_test` | 1 | **Test artifact, drop** |
| 19 other tables | 0 | Empty — see §3 |

Total ~86k rows across 35 base tables. 32 views defined; 3 are A/B-specific, 4 are eval-specific, 1 is `vw_legacy_judge_quality`.

### 1.2 Tool coverage in `tool_calls`

Tools with recorded activity (the MCP actually executes these):

| `tool_name` | requests | responses | errors |
|---|---:|---:|---:|
| `get_content` | 366 | 329 | 0 |
| `web_search` | 222 | 142 | 3 |
| `batch_get_content` | 112 | 53 | 0 |
| `quick_web_search` | 60 | 60 | 0 |
| `discover_links` | 23 | 10 | 0 |
| `gemini_search` | 18 | 18 | 0 |
| `generate_sitemap` | 1 | 0 | 0 |

Tools in `TOOL_CATALOG` (catalog.py) **with zero recorded calls**: `youtube_search`, `youtube_transcript`, `academic_search`, `grok_search`, `composio_similarlinks`. These are exercised only outside the current analytics window or never.

### 1.3 LLM-judgment coverage

`llm_judgments` already shows the 6-facet FlockMTL pipeline running:

| `judgment_kind` | rows |
|---|---:|
| `result_quality` | 1,324 |
| `rerank_improvement` | 344 |
| `intent_coherence` | 204 |
| `rewrite_coverage` | 165 |
| `run_overview` | 235 |
| `judge_rewrite` | 12 |
| `grade_relevance` | 45 |
| `failure_cause` | 2 |

The `judge_evaluations` table (6 rows, legacy 4-facet schema) is effectively dead — `llm_judgments` is the new source of truth.

---

## 2. NULL hotspots — what's wired and what's not

### 2.1 `search_runs` (502 rows)

| column | NULL% | comment |
|---|---:|---|
| `session_id` | **100.00** | never set; required for cohort analysis |
| `brave_spellcheck` | **100.00** | dead — drop |
| `tool_call_id` | **99.60** | breaks join to `tool_calls` |
| `error_type` | 96.81 | OK (most runs succeed) |
| `rewrite_error` | 89.44 | OK |
| `rewritten_branch_queries` | 37.65 | only set when rewrite_enabled |
| `rewrite_*` (model/tokens/latency) | 18.73 | only set when rewrite_enabled |
| `reranker_provider/model` | 3.39 | healthy |

### 2.2 `provider_calls` (6,243 rows)

| column | NULL% | comment |
|---|---:|---|
| `request_url` | 96.57 | typed field; should be 100% on success — many providers don't write it |
| `http_status` | 96.03 | only 4% of calls record HTTP status; untyped errors are invisible |
| `request_query` | 62.81 | search engine URL params never set |
| `result_class` | 62.81 | `success`/`empty`/`timeout`/`incomplete` enum never set |
| `response_meta_json` | 62.81 | response metadata never captured |
| `error_type`/`error_message` | 57.22 | OK on success, blank on error path |

### 2.3 `rerank_candidates` (32,413 rows)

| column | NULL% | comment |
|---|---:|---|
| `bm25_score`, `bm25_rank` | **100.00** | BM25 stage is not writing |
| `dense_score`, `dense_rank` | **100.00** | dense (bi-encoder) stage is not writing |
| `fused_score`, `hybrid_rrf_score` | **100.00** | hybrid fusion not implemented |
| `recency_boost`, `entity_overlap_score` | **100.00** | rerank feature knobs not implemented |
| `llm_raw_score` | **100.00** | per-candidate LLM score never persisted |
| `score_after`, `rank_after` | 53.65 | only set on stages that emit post-rank |
| `cross_encoder_raw` | 18.46 | OK — only cross-encoder rows |

### 2.4 `rerank_stages` (1,404 rows)

| column | NULL% | comment |
|---|---:|---|
| `score_threshold`, `alpha_blend` | **100.00** | rerank config knobs not recorded |
| `instruction_present`, `instruction_length`, `query_type_hint` | **100.00** | instruction-conditioning not recorded |
| `entity_overlap_enabled` | **100.00** | entity-aware rerank toggle not recorded |
| `error_type` | 85.54 | OK |
| `input_tokens`/`output_tokens` | 81.13 | LLM-rerank stage costs not tracked |
| `max_score`/`avg_score` | 66.67 | OK — only LLM stages emit scores |
| `model` | 47.79 | BM25/dense stages have no model — OK |
| `provider` | 33.40 | OK |

### 2.5 `tool_calls` (1,417 rows)

| column | NULL% | comment |
|---|---:|---|
| `output_tokens`, `input_tokens` | 100.00 | cost attribution per tool call impossible today |
| `normalized_url` | 100.00 | URL canonicalization not recorded |
| `error_type` | 99.79 | errors not typed |
| `session_id` | 99.29 | sessions not correlated |
| `model` | 98.45 | tool caller's model not recorded |
| `provider` | 95.77 | tool caller's provider not recorded |
| `error_message` | 94.00 | |
| `input_count` | 86.45 | |
| `duration_ms` | 84.83 | half the rows are `request` phase (no duration yet) |
| `output_count` | 79.89 | |
| `input_url` | 71.77 | OK — only set for `get_content`/`discover_links` |
| `query` | 66.97 | OK — request/response mix |
| `request_fingerprint` | 43.40 | OK |
| `trace_id`, `span_id` | 25.76 | partial — OpenTelemetry path used for some tools only |

### 2.6 What this means

- **The cost story is broken.** The `llm_call_log` table is the unified LLM cost source (281 rows), but `tool_calls` never records `input_tokens`/`output_tokens`/`cost_usd` — so you cannot answer "what does it cost to run a `web_search`?" in terms of LLM dollars.
- **The error story is broken.** Only `provider_calls` records `error_type`/`http_status` and even there it's 57% / 96% NULL. The `tool_calls` errors are 99.79% missing `error_type`.
- **The session story is broken.** `session_id` is empty on `search_runs`, 99% empty on `tool_calls`. Cohort, funnels-by-session, and any "what did this user do in the last 5 minutes?" query is impossible.

---

## 3. Empty tables — what's the intent?

### 3.1 `summary_*_daily` (4 tables, 0 rows each)

```sql
summary_provider_daily(day, provider, query_count, avg_results_returned,
                      p50_results_returned, avg_latency_ms, p50_latency_ms,
                      p95_latency_ms, error_rate, distinct_queries)
summary_intent_daily(day, intent, query_count, avg_confidence, avg_branch_count)
summary_quality_daily(day, avg_overlap_rate, avg_domain_diversity,
                      avg_domain_diversity_ratio, avg_compression_ratio, avg_top_score)
summary_rerank_daily(day, stage, provider, runs_count, avg_compression_ratio,
                     avg_max_score, p50_latency_ms, p95_latency_ms, entity_overlap_runs)
```

All four have DDL, all four are empty. `summaries.py` provides a `refresh_daily_aggregates` function, but it is **not called from anywhere in the runtime** — verified by grep on the package. The whole pattern is a "materialized table" approach (DuckDB has no `CREATE MATERIALIZED VIEW`; see §4.2) that is never refreshed.

### 3.2 `ab_*` (5 tables, 0 rows)

`ab_experiments`, `ab_experiment_variants`, `ab_assignments`, `ab_results`, `ab_shadow_runs`. DDL is defined; views `v_ab_experiment_summary`, `v_ab_variant_comparison`, `v_ab_shadow_run_analysis` reference them. Empty.

### 3.3 `eval_*` (8 tables, 0 rows)

`eval_runs`, `eval_cases`, `eval_observations`, `eval_candidate_sets`, `eval_tool_calls`, `eval_scores`, `eval_judge_calls`, `eval_failures`. Views: `vw_eval_case_timeline`, `vw_eval_candidate_survival`, `vw_eval_provider_quality`, `vw_eval_pass_rate`. All empty.

### 3.4 `judge_rubrics`, `judge_calibration_set` (0 rows each)

`judge_rubrics` is meant to be the rubric catalog; `judge_calibration_set` is meant to be the human-adjudicated subset for Cohen's κ. Both empty — calibration cannot run, Brier scores in `vw_query_understanding_calibration` will return `NULL`.

### 3.5 `provider_health_transitions` (0 rows)

Schema exists, but no `circuit_state` events recorded. Combined with the absence of `result_class` on `provider_calls`, circuit-breaker analytics are entirely blind.

### 3.6 `llm_quality_scores` (0 rows)

Another empty table from the `eval_*` family. Not referenced by any view; safe to drop.

### 3.7 `analytics_sync_state` (0 rows)

MotherDuck sync state — only relevant if sync is configured. Otherwise dead.

### 3.8 `flockmtl_resources` (11 rows)

The only non-empty "metadata" table. This is fine.

---

## 4. DuckDB best-practice check

### 4.1 Indexes — current state

15 ART indexes, 4 HNSW indexes. Listing:

```
idx_cemb_hnsw        candidate_embeddings(embedding) USING HNSW
idx_cemb_run_key     candidate_embeddings(run_key) ART
idx_llm_call_log_purpose   llm_call_log(call_purpose) ART
idx_llm_call_log_run_key   llm_call_log(run_key) ART
idx_llm_judgments_kind     llm_judgments(judgment_kind) ART
idx_llm_judgments_run_key  llm_judgments(run_key) ART
_t_idx2              query_embeddings(embedding) USING HNSW   ← DUPLICATE
idx_qemb_hnsw        query_embeddings(embedding) USING HNSW
idx_qemb_run_key     query_embeddings(run_key) ART
idx_query_understanding_intent_recorded  query_understanding_events(predicted_intent, recorded_at) ART
idx_query_understanding_run_key          query_understanding_events(run_key) ART
idx_runs_recorded_at search_runs(recorded_at) ART
idx_runs_run_key     search_runs(run_key) ART
idx_tool_calls_status          tool_calls(status) ART
idx_tool_calls_tool_call_id    tool_calls(tool_call_id) ART
idx_tool_calls_tool_recorded   tool_calls(tool_name, recorded_at) ART
```

**Findings:**

- **DuckDB 1.4.1 LTS fixed an ART index multi-threaded non-determinism bug** ([release notes](https://duckdb.org/2025/10/07/announcing-duckdb-141.html)). Worth a note in the AGENTS.md: if you ever see "row omitted" results, it's the index, not the data.
- **ART indexes are for very-highly-selective lookups (<0.1%)** per [DuckDB docs](https://duckdb.org/docs/current/sql/indexes.html). Most of the indexes here are reasonable for "find a row by `run_key`" but the 2-column `(predicted_intent, recorded_at)` and `(tool_name, recorded_at)` are weaker — DuckDB will only use the prefix. That's actually fine for the dashboard time-series queries.
- **Missing ART indexes that would help** (added below in §5.1):
  - `tool_calls(tool_call_id, phase)` — composite to support `WHERE tool_call_id=? AND phase='response'` (used in dashboard latency joins).
  - `final_results(run_key, rank)` — for ranked subqueries.
  - `search_candidates(run_key, link)` — PK for the join.
  - `llm_judgments(recorded_at)` — for daily aggregations on the judge view.
- **HNSW persistence caveat:** the `vss` extension requires `SET hnsw_enable_experimental_persistence = true;` per connection (already done in `ensure_vss_loaded`). The duplicate `_t_idx2` on `query_embeddings` is auto-generated by the extension in some code paths and wastes ~80% of the file's footprint on the second index. Drop the duplicate.

### 4.2 Materialized views / tables — current state

**DuckDB has no `CREATE MATERIALIZED VIEW` in 1.5.2.** Verified empirically:

```
Parser Error: syntax error at or near "MATERIALIZED"
LINE 1: CREATE MATERIALIZED VIEW mv_test AS ...
```

The only "materialization" primitive is **CTAS** (`CREATE TABLE foo AS SELECT ...`) with manual refresh (`DROP TABLE foo; CREATE TABLE foo AS SELECT ...;`). Several online sources citing `REFRESH MATERIALIZED VIEW` are wrong about stock DuckDB.

**Your four `summary_*_daily` tables** are the right shape for this pattern. The gap is the refresh job.

### 4.3 Table partitioning — current state

DuckDB v0.9+ removed native `PARTITION BY` on regular tables. Partitioning is now done at the **file layout** level (Hive-style `PARTITION_BY (year, month)` on `COPY ... TO`) or via DuckLake.

For your size (~86k rows, 181 MB), **no partitioning is needed**. The whole DB fits in memory; ART indexes are sufficient. If you cross ~10M rows or want to publish a data mart, consider exporting the largest read-only table to a Parquet file with `PARTITION_BY (recorded_at)` for archive/BI use.

### 4.4 Joins & column orientation

DuckDB is columnar and will vectorize joins automatically. No special config needed. A few minor cleanups:

- `provider_calls` has 19 columns; `error_message` is a free-form VARCHAR (no length cap). DuckDB doesn't penalize wide tables, but the readers will. Consider `VARCHAR(2048)` cap on long-text columns.
- `payload_json` everywhere is good. The legacy tables (`judge_evaluations.payload_json`) are JSON; the eval_* tables use `VARCHAR` (not `JSON`) — inconsistent. Migrate eval_* to `JSON` for consistency.
- `search_runs.rewritten_branch_queries` is `VARCHAR[]` — good, no `JSON` parsing needed at the array level. The typed `rewritten_branch_queries[0..4]` convention (`k1, k2, k3, neural, specialized`) should also be a separate typed table, e.g. `search_run_rewrites(run_key, slot, query)` (see §5.1).

### 4.5 JSON access patterns

- `vw_result_quality_diagnostics` uses `json_extract(lj.payload_json, '$.parsed.intent_match')` — that means the judge pipeline is stuffing structured data into a JSON blob instead of using typed columns. **Promote these to typed columns** on `llm_judgments`: `intent_match BOOLEAN`, `informativeness SMALLINT`, plus 3–4 more for the other facets. This will let ART indexes serve the query, and it makes the data human-readable in `DESCRIBE`.
- `vw_run_funnel_by_stage` reads `$.funnel_counts.{input_count,bi_output_count,…}` and `$.phase_timings.{search.plan,…}` from `search_runs.payload_json`. Same recommendation: add typed columns `bi_output_count`, `cross_output_count`, `rankllm_output_count`, `phase_plan_ms`, `phase_retrieve_ms`, `phase_rank_ms` directly on `search_runs`. Will eliminate the `try_cast(json_extract(...))` in views and make them indexable.

### 4.6 DuckLake / MotherDuck

`analytics_sync_state` exists for MotherDuck sync but is empty. MotherDuck support in DuckDB 1.4+ is stable. If you ever go that direction, the table is fine. If not, drop it.

---

## 5. Recommendations

### 5.1 Schema changes (new columns / tables)

#### **P0 — must fix to restore the join graph**

1. **Add `tool_calls.run_key VARCHAR`** and backfill from in-process correlation. Create composite index:
   ```sql
   CREATE INDEX idx_tool_calls_run_phase ON tool_calls(run_key, phase);
   ```
2. **Backfill `search_runs.tool_call_id` and `search_runs.session_id`** from the call-site that produces the run. Both are populated by the upstream MCP call; the writer is dropping them.
3. **Add `provider_calls.run_key`** (or ensure the existing `run_key` column is populated — it is, but only via the writer path). Verify with:
   ```sql
   SELECT COUNT(*) FROM provider_calls WHERE run_key IS NULL;
   ```
   It should be 0 in steady state; if not, the writer is dropping it.

#### **P1 — promote JSON to typed columns**

4. **Add typed columns to `search_runs`**:
   ```sql
   ALTER TABLE search_runs ADD COLUMN bi_output_count INTEGER;
   ALTER TABLE search_runs ADD COLUMN cross_output_count INTEGER;
   ALTER TABLE search_runs ADD COLUMN rankllm_output_count INTEGER;
   ALTER TABLE search_runs ADD COLUMN phase_plan_ms DOUBLE;
   ALTER TABLE search_runs ADD COLUMN phase_retrieve_ms DOUBLE;
   ALTER TABLE search_runs ADD COLUMN phase_rank_ms DOUBLE;
   ```
   Rewrite `vw_run_funnel_by_stage` to read from typed columns; drop the `try_cast(json_extract(...))` paths.
5. **Add typed columns to `llm_judgments`** for the per-facet scores:
   ```sql
   ALTER TABLE llm_judgments ADD COLUMN intent_match BOOLEAN;
   ALTER TABLE llm_judgments ADD COLUMN informativeness SMALLINT;
   ALTER TABLE llm_judgments ADD COLUMN coverage_score DOUBLE;
   ALTER TABLE llm_judgments ADD COLUMN rerank_improvement_delta DOUBLE;
   ALTER TABLE llm_judgments ADD COLUMN overview_score DOUBLE;
   ALTER TABLE llm_judgments ADD COLUMN failure_root_cause VARCHAR;
   ```
   Keep `payload_json` for the raw LLM response, but make the typed values primary. This lets the quality-miss view, judge-facet-agg view, and result-quality-diagnostics view all use plain columns with indexable predicates.

#### **P2 — new tables that close specific gaps**

6. **`search_run_rewrites(run_key, slot, query, source_branch)`** — typed array-of-arrays substitute for `search_runs.rewritten_branch_queries`. Currently that array is used to store 5 strings (k1, k2, k3, neural, specialized); making it a child table means you can join on slot cleanly.
7. **`session_dim(session_id PRIMARY KEY, started_at, last_seen_at, tool_call_count, run_count)`** — slowly-changing dim so you can group any fact by session. Populate via `MERGE` from `search_runs` + `tool_calls` on `session_id`.
8. **`provider_daily_health(day, provider, p50_latency, p95_latency, error_rate, circuit_open_seconds)`** — derived from `provider_health_transitions` + `provider_calls`. This is the kind of view that the `vw_provider_reliability_daily` view approximates, but typed/refreshed.

#### **P3 — replace the empty `summary_*_daily` with refreshable CTAS**

Replace the four empty DDL-managed tables with a single `summaries.py` workflow that does:

```sql
-- refresh on demand; safe to run every N minutes because CTAS is atomic
CREATE OR REPLACE TABLE summary_provider_daily AS
SELECT
  CAST(recorded_at AS DATE) AS day,
  provider,
  COUNT(*) AS query_count,
  AVG(num_results_returned) AS avg_results_returned,
  quantile_cont(num_results_returned, 0.50) AS p50_results_returned,
  AVG(latency_ms) AS avg_latency_ms,
  quantile_cont(latency_ms, 0.50) AS p50_latency_ms,
  quantile_cont(latency_ms, 0.95) AS p95_latency_ms,
  AVG(CASE WHEN status <> 'success' THEN 1.0 ELSE 0.0 END) AS error_rate,
  COUNT(DISTINCT request_query) AS distinct_queries
FROM provider_calls
WHERE recorded_at >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY 1, 2;
```

Run all four `CREATE OR REPLACE` once at server startup, then again on a configurable interval (e.g., 5–15 min), guarded by `settings.analytics_summary_refresh_enabled`.

### 5.2 Index changes

```sql
-- 1. Drop duplicate HNSW (verified by listing duckdb_indexes())
DROP INDEX IF EXISTS _t_idx2;  -- the auto-generated one on query_embeddings

-- 2. Add ART indexes that the dashboard views need
CREATE INDEX IF NOT EXISTS idx_final_results_run_rank ON final_results(run_key, rank);
CREATE INDEX IF NOT EXISTS idx_search_candidates_run_link ON search_candidates(run_key, link);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run_phase ON tool_calls(run_key, phase);
CREATE INDEX IF NOT EXISTS idx_llm_judgments_recorded_at ON llm_judgments(recorded_at);
CREATE INDEX IF NOT EXISTS idx_rerank_candidates_run_stage_link ON rerank_candidates(run_key, stage, link);
CREATE INDEX IF NOT EXISTS idx_provider_calls_run_provider ON provider_calls(run_key, provider);

-- 3. The existing vw_candidate_funnel view runs 4 correlated subqueries against
--    rerank_candidates per row of search_candidates. A composite index on
--    (run_key, link, stage) lets the optimizer replace them with index seeks.
```

### 5.3 View changes

#### New views

1. **`vw_tool_call_costs_by_model`** — for the cost story. Until `tool_calls.input_tokens`/`output_tokens` is populated, derive cost from `llm_call_log` (it has `cost_usd`) joined on `run_key` (once the join is repaired).
   ```sql
   CREATE OR REPLACE VIEW vw_tool_call_costs_by_model AS
   SELECT
     tc.tool_name,
     lcl.provider,
     lcl.model,
     lcl.call_purpose,
     COUNT(*) AS call_count,
     SUM(lcl.input_tokens)  AS total_input_tokens,
     SUM(lcl.output_tokens) AS total_output_tokens,
     SUM(lcl.cost_usd)      AS total_cost_usd
   FROM tool_calls tc
   JOIN llm_call_log lcl ON lcl.run_key = tc.run_key
   GROUP BY ALL;
   ```

2. **`vw_session_summary`** — once `session_id` is populated.
   ```sql
   CREATE OR REPLACE VIEW vw_session_summary AS
   SELECT
     s.session_id,
     MIN(s.recorded_at) AS started_at,
     MAX(s.recorded_at) AS last_seen_at,
     COUNT(DISTINCT s.run_key) AS runs,
     COUNT(DISTINCT tc.event_id) AS tool_events,
     SUM(lcl.cost_usd) AS total_cost_usd
   FROM search_runs s
   LEFT JOIN tool_calls tc ON tc.run_key = s.run_key
   LEFT JOIN llm_call_log lcl ON lcl.run_key = s.run_key
   WHERE s.session_id IS NOT NULL
   GROUP BY 1;
   ```

3. **`vw_run_funnel_v2`** — replaces `vw_run_funnel_by_stage` using typed columns (P1 above).

4. **`vw_provider_error_clusters`** — diagnostics view: groups provider errors by `(provider, http_status, error_type)` for SRE triage.
   ```sql
   CREATE OR REPLACE VIEW vw_provider_error_clusters AS
   SELECT
     provider,
     COALESCE(http_status::VARCHAR, 'no_status') AS http_status,
     COALESCE(error_type, 'no_type')              AS error_type,
     COUNT(*) AS occurrences,
     MIN(recorded_at) AS first_seen,
     MAX(recorded_at) AS last_seen,
     list(DISTINCT request_query ORDER BY request_query) FILTER (WHERE request_query IS NOT NULL) AS sample_queries
   FROM provider_calls
   WHERE status <> 'success'
   GROUP BY ALL
   ORDER BY occurrences DESC;
   ```

5. **`vw_empty_runs_triage`** — companion to `vw_bad_case_queue`, focuses on the 18% of `search_candidates` with empty `domain` (5702 rows), grouped by `provider`/`branch_role`.

#### Views to remove (after their source tables are removed)

- `vw_legacy_judge_quality` — once `judge_evaluations` is removed.
- All 4 `vw_eval_*` views — once the 8 `eval_*` tables are removed (or until the eval harness produces data).

#### Views to keep but rewire

- `vw_provider_reliability_daily` — keep, but make sure it reads from typed columns. Currently fine.

### 5.4 Tables to drop

| Table | Why |
|---|---|
| `_hnsw_test` | Dev leak, 1 row |
| `judge_evaluations` (and `vw_legacy_judge_quality`) | Superseded by `llm_judgments` (which already has 2,331 rows vs 6) |
| `judge_rubrics`, `judge_calibration_set` | Empty AND not wired — pick a side: either wire them from `judges.py` or drop. Recommend: drop `judge_evaluations`, KEEP `judge_rubrics` (it's the catalog) but populate it from `judges.py` at rubric registration. KEEP `judge_calibration_set` but wire it from a calibration CLI/UI. |
| `llm_quality_scores` | Empty, no view references it |
| `analytics_sync_state` | Only needed if MotherDuck sync is configured; keep as no-op otherwise |
| `ab_*` (5 tables) + `v_ab_*` (3 views) | If A/B framework is unused, drop. If used, wire the writer. |
| `eval_*` (8 tables) + `vw_eval_*` (4 views) | If eval harness is unused, drop. If used, the writer path is missing. |
| `provider_health_transitions` | Schema correct but no events. Either wire from `circuit_breaker` or drop. |

### 5.5 Column-level cleanups

- `search_runs.brave_spellcheck` — 100% NULL, drop.
- `tool_calls.normalized_url` — 100% NULL, either populate from the URL canonicalizer or drop.
- `tool_calls.input_tokens` / `output_tokens` — 100% NULL, must populate. (See §2.5.)
- `rerank_candidates.bm25_*` / `dense_*` / `fused_score` / `hybrid_rrf_score` / `recency_boost` / `entity_overlap_score` / `llm_raw_score` — 100% NULL. Either populate or remove. (See §2.3.)
- `rerank_stages.score_threshold` / `alpha_blend` / `instruction_*` / `query_type_hint` / `entity_overlap_enabled` — 100% NULL. Either populate from the rerank config or remove. (See §2.4.)
- `final_results.candidate_id` / `canonical_result_id` — 100% NULL. These were meant to be the lineage IDs from `rerank_candidates.candidate_id`; either populate or remove.

### 5.6 Coverage matrix — MCP tool → analytics tables → coverage

| MCP tool | `tool_calls` | `search_runs` | `provider_calls` | `llm_call_log` | judge coverage | missing |
|---|:-:|:-:|:-:|:-:|:-:|---|
| `web_search` | ✅ (222 req) | ✅ | ✅ per branch | partial (rewrite only) | run_overview, intent_coherence, rewrite_coverage | LLM cost per call (only rewrite counted) |
| `quick_web_search` | ✅ (60) | ✅ | ✅ per branch | partial | intent_coherence | cost per call |
| `get_content` | ✅ (366) | n/a | n/a | n/a | n/a | LLM cost (zero today; `get_content` may use LLM extraction) |
| `batch_get_content` | ✅ (112) | n/a | n/a | n/a | n/a | LLM cost |
| `discover_links` | ✅ (23) | n/a | n/a | n/a | n/a | LLM cost |
| `gemini_search` | ✅ (18) | n/a | n/a | n/a | result_quality on output | cost |
| `generate_sitemap` | ✅ (1) | n/a | n/a | n/a | n/a | cost, error type |
| `youtube_search` | ❌ 0 rows | n/a | n/a | n/a | n/a | **not instrumented at all** |
| `youtube_transcript` | ❌ 0 rows | n/a | n/a | n/a | n/a | **not instrumented at all** |
| `academic_search` | ❌ 0 rows | n/a | n/a | n/a | n/a | **not instrumented at all** |
| `grok_search` | ❌ 0 rows | n/a | n/a | n/a | n/a | **not instrumented at all** |
| `composio_similarlinks` | ❌ 0 rows | n/a | n/a | n/a | n/a | **not instrumented at all** |

**Coverage gaps for the public-facing tool surface:**

- 5 of 12 tools (`youtube_search`, `youtube_transcript`, `academic_search`, `grok_search`, `composio_similarlinks`) emit **zero** `tool_calls` rows. Either the writer middleware isn't installed on these tools, or they haven't been exercised. The `tool_calls` table is the most natural place to add coverage.
- Cost attribution is partial. The MCP can answer "what did this run cost in LLM dollars?" only for runs that did a rewrite (because the writer path goes through `llm_call_log.call_purpose='rewrite'`). For `get_content` / `batch_get_content` (which use LLM extraction) and `gemini_search` (which IS an LLM), there's no cost column.
- Judge coverage is per-facet on `web_search` runs but never on `youtube_search` / `gemini_search` / `academic_search` outputs.

### 5.7 Coverage — MCP resources

| Resource | Backing | Status |
|---|---|---|
| `analytics://schema` | `_analytics_schema_snapshot()` | live |
| `analytics://candidate-survival` | `vw_eval_candidate_survival` | live but on empty `eval_cases` |
| `analytics://reports/{name}{?days}` | `reports.py` (deterministic catalog) | live |
| `analytics://providers/status` (planned) | — | missing |
| `analytics://costs/attribution` (planned) | — | missing |
| `analytics://runs/{run_key}` (planned) | — | missing |

**Recommendation:** the user profile says they prefer **extending the existing surface** (more resources / views) over adding new MCP tools. Two natural extensions to the resource catalog:

1. `analytics://cost-attribution?days=7` — bound to the new `vw_tool_call_costs_by_model` view (§5.3 #1).
2. `analytics://provider-errors?days=7` — bound to the new `vw_provider_error_clusters` view (§5.3 #4).
3. `analytics://sessions/{session_id}` — bound to the new `vw_session_summary` view + `search_runs` (once `session_id` is populated).

These keep the resource-first pattern and use no new MCP tool.

---

## 6. What to do this week (in order)

1. **Decide on the dead/empty tables** before changing the schema. The drop list in §5.4 is reversible if you have DDL under version control.
2. **Fix the join** (P0 items in §5.1 #1–#3) — without this, you cannot compute cost or error attribution per run.
3. **Wire `summaries.py` to a refresh job** — five lines in `summaries.py` + a `ThreadingTimer` or `apscheduler` call. Until then the four `summary_*_daily` tables are dead.
4. **Promote JSON to typed columns** (P1 in §5.1 #4–#5). Biggest single performance + readability win.
5. **Add the missing indexes** (§5.2) — 6 statements, safe and idempotent.
6. **Drop the duplicate HNSW index** and the `_hnsw_test` table.

After those, the schema will be small enough to reason about (~25 tables, ~20 views) and the join graph will support the actual MCP surface.

---

## 7. Open questions for the user

1. Are the 5 un-instrumented tools (`youtube_search`, `youtube_transcript`, `academic_search`, `grok_search`, `composio_similarlinks`) intentionally not instrumented, or is the middleware just not wired? The tool_calls writer is in `writers/inserts.py`; need to check whether these tools call `dispatch_duckdb_write` like the others.
2. Is the A/B framework (`ab_*` tables + `v_ab_*` views) intended to be wired into the search outcome path, or is it aspirational? If aspirational, drop to keep the public schema clean.
3. Is the eval harness (`eval_*` tables) actively used? If yes, the writer is broken (8 tables, 0 rows). If no, drop.
4. For the four `summary_*_daily` tables: refresh on server startup + every N minutes, or only on a cron expression? The existing `summaries.py` provides the SQL but no scheduler entry point.
5. Are the typed columns on `llm_judgments` (intent_match, informativeness, etc.) extractable from `payload_json` cleanly today, or is the LLM response unstructured? This determines whether the P1 typed-column migration can be done by backfill or needs a judge-pipeline change.

---

## 8. Files to read / change for the implementation

- `src/kindly_web_search_mcp_server/analytics/writers/schema.py` — DDL for the 7 fact tables
- `src/kindly_web_search_mcp_server/analytics/writers/ab_schema.py` — A/B DDL
- `src/kindly_web_search_mcp_server/analytics/writers/summary_schema.py` — `summary_*_daily` DDL
- `src/kindly_web_search_mcp_server/analytics/evals.py` — `eval_*` DDL
- `src/kindly_web_search_mcp_server/analytics/views.py` — 23 dashboard views
- `src/kindly_web_search_mcp_server/analytics/duckdb_store.py` — facade
- `src/kindly_web_search_mcp_server/analytics/summaries.py` — needs a refresh trigger
- `src/kindly_web_search_mcp_server/analytics/writers/inserts.py` — column lists
- `src/kindly_web_search_mcp_server/analytics/AGENTS.md` — update after the change

The pattern is well-established: `_create_table()` for DDL, `_ensure_columns()` for ALTER, `TableWriter` for non-blocking inserts, `dispatch_duckdb_write()` for the hot path. All changes stay inside the existing writer lock, so concurrent analytics writers are unaffected.

---

*This report was generated by inspecting the live DuckDB (181 MB, 86,411 rows) via read-only queries against a copy, the 36 files in `src/kindly_web_search_mcp_server/analytics/`, and the public DuckDB 1.5.2 documentation. The recommendations are prioritized by impact on the join graph and on the resources the MCP actually exposes to clients.*
