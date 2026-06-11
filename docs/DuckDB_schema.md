# DuckDB Schema Reference

> Full schema documentation for the web-search-mcp analytics and A/B testing database.

**21 tables** · **13 views** · **3 modules** (Analytics, A/B Testing, Summaries)

---

## Table of Contents

1. [Raw Event Store](#1-search_events-raw-event-store)
2. [Search Pipeline Tables (Tables 5–10)](#search-pipeline-tables)
3. [Quality & Judge Tables (Tables 11–16)](#quality--judge-tables)
4. [A/B Testing Tables (Tables 17–21)](#ab-testing-tables)
5. [Summary Tables (Tables 12–15)](#summary-tables)
6. [Views](#views)
7. [Module Map](#module-map)
8. [Settings Reference](#settings-reference)
9. [A/B Pipeline Wiring](#ab-pipeline-wiring)
10. [ER Relationships](#er-relationships)

---

## 1. search_events (raw event store)

**Table name:** `search_events`
**DDL location:** `duckdb_store.py` → `_ensure_schema()`
**Write function:** `append_event()`
**Purpose:** Generic append-only observability event store. Every observability event (any phase, any tool) lands here first. Column values are normalised from `payload_json` via backfill UPDATEs.

### Columns

| Column | Type | Notes |
|---|---|---|
| `event_id` | `VARCHAR` | Auto-generated UUID |
| `event_name` | `VARCHAR` | Dot-notation e.g. `web_search.provider_call` |
| `recorded_at` | `TIMESTAMP` | Set to `CURRENT_TIMESTAMP` on insert |
| `run_key` | `VARCHAR` | Extracted from `trace_id` or `request_fingerprint` in payload |
| `tool_name` | `VARCHAR` | Extracted from `$.tool_name` in payload_json |
| `phase` | `VARCHAR` | Last component of `event_name` after `.` |
| `query` | `VARCHAR` | Original query from payload |
| `normalized_query` | `VARCHAR` | Normalised query from payload |
| `research_goal` | `VARCHAR` | Research goal from payload |
| `provider` | `VARCHAR` | Coalesced from `$.provider`, `$.provider_name` |
| `model` | `VARCHAR` | Model identifier from payload |
| `duration_ms` | `DOUBLE` | Coalesced from `$.duration_ms` or `$.duration_seconds * 1000` |
| `input_count` | `INTEGER` | Coalesced from multiple payload keys |
| `output_count` | `INTEGER` | Coalesced from multiple payload keys |
| `trace_id` | `VARCHAR` | Trace identifier from payload |
| `span_id` | `VARCHAR` | Span identifier from payload |
| `cache_hit` | `VARCHAR` | Cache status from payload |
| `payload_json` | `VARCHAR` | Full original payload as JSON string |

### Notes

- **Backfill strategy:** Columns that can be derived from `payload_json` are backfilled via UPDATE statements after table creation. New rows always get explicit values.
- **Not primary-keyed** — designed as a write-once, append-only log.

---

## Search Pipeline Tables

These 9 tables capture the lifecycle of a single web-search run through the pipeline: run start → query understanding → query rewriting → provider calls → provider candidates → merge → rerank → final results.

### 2. search_runs

**Table name:** `search_runs`
**DDL location:** `duckdb_store.py` → `_ensure_search_runs()`
**Write function:** `insert_search_run()`
**Purpose:** One row per search pipeline invocation. Root entity for the entire run lifecycle.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | Unique run identifier |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `query` | `VARCHAR NOT NULL` | Original user query |
| `normalized_query` | `VARCHAR` | Normalised/cleaned query |
| `research_goal` | `VARCHAR` | Inferred user research goal |
| `num_results_requested` | `INTEGER` | How many results the caller requested |
| `rewrite_enabled` | `BOOLEAN` | Whether query rewriting was active |
| `session_id` | `VARCHAR` | User session identifier |
| `tool_name` | `VARCHAR` | `DEFAULT 'web_search'` |
| `duration_ms` | `DOUBLE` | Total pipeline duration |
| `final_result_count` | `INTEGER` | Count of results returned to user |
| `candidate_count` | `INTEGER` | Total raw candidates gathered |
| `has_more` | `BOOLEAN` | Whether more results exist beyond returned set |
| `result_offset` | `INTEGER` | Pagination offset |
| `status` | `VARCHAR` | Run status (success, error, timeout, etc.) |
| `error_type` | `VARCHAR` | Error classification if run failed |
| `payload_json` | `JSON` | Full run payload |

#### Indexes

| Index Name | Columns |
|---|---|
| `idx_runs_run_key` | `run_key` |
| `idx_runs_recorded_at` | `recorded_at` |

---

### 3. query_understanding

**Table name:** `query_understanding`
**DDL location:** `duckdb_store.py` → `_ensure_query_understanding()`
**Write function:** `insert_query_understanding()`
**Purpose:** LLM-based query classification results — intent, decomposition decision, entities, time sensitivity.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `intent` | `VARCHAR` | Classified intent (informational, navigational, transactional, etc.) |
| `confidence` | `DOUBLE` | LLM confidence in classification (0–1) |
| `should_decompose` | `BOOLEAN` | Whether query should be decomposed into sub-queries |
| `rationale` | `VARCHAR` | LLM reasoning for classification |
| `model` | `VARCHAR` | LLM model used |
| `provider` | `VARCHAR` | LLM provider used |
| `duration_ms` | `DOUBLE` | Time taken for classification |
| `fallback_used` | `BOOLEAN` | Whether fallback classification was used |
| `entities_count` | `INTEGER` | Number of named entities detected |
| `preserved_terms` | `VARCHAR[]` | Array of terms preserved from original query |
| `time_sensitivity` | `VARCHAR` | Time sensitivity classification (fresh, recent, evergreen) |
| `payload_json` | `JSON` | Full payload |

---

### 4. query_rewrites

**Table name:** `query_rewrites`
**DDL location:** `duckdb_store.py` → `_ensure_query_rewrites()`
**Write function:** `insert_query_rewrites()`
**Purpose:** Rewritten query variants — each row is one variant of the original query for multi-branch search.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `variant_index` | `INTEGER` | Index of this variant (0-based) |
| `branch_type` | `VARCHAR` | Type of branching (semantic, decomposition, synonym, etc.) |
| `kind` | `VARCHAR` | Kind of rewrite (expansion, reformulation, translation) |
| `target` | `VARCHAR` | Target provider or domain for this variant |
| `query` | `VARCHAR NOT NULL` | The rewritten query text |
| `weight` | `DOUBLE` | Branch weight for merging |
| `reason` | `VARCHAR` | LLM rationale for this rewrite |
| `max_results` | `INTEGER` | Max results to request for this variant |
| `model` | `VARCHAR` | LLM model used |
| `duration_ms` | `DOUBLE` | Time taken for rewrite |
| `payload_json` | `JSON` | Full payload |

---

### 5. provider_calls

**Table name:** `provider_calls`
**DDL location:** `duckdb_store.py` → `_ensure_provider_calls()`
**Write function:** `insert_provider_calls()`
**Purpose:** Every outbound API call to a search provider (Google, Bing, Tavily, SerpAPI, etc.).

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `provider` | `VARCHAR NOT NULL` | Provider name |
| `branch_index` | `INTEGER` | Which query branch this call belongs to |
| `branch_query` | `VARCHAR` | The query sent to this provider |
| `num_results_requested` | `INTEGER` | Results requested |
| `num_results_returned` | `INTEGER` | Results actually returned |
| `duration_ms` | `DOUBLE` | Provider API latency |
| `error_code` | `VARCHAR` | Error code if call failed |
| `error_message` | `VARCHAR` | Error message if call failed |
| `http_status` | `INTEGER` | HTTP status code |
| `tokens_used` | `INTEGER` | Token consumption (if LLM-based provider) |
| `cost_usd` | `DOUBLE` | Estimated cost in USD |
| `payload_json` | `JSON` | Full payload |

---

### 6. provider_candidates

**Table name:** `provider_candidates`
**DDL location:** `duckdb_store.py` → `_ensure_provider_candidates()`
**Write function:** `insert_provider_candidates()`
**Purpose:** Individual search results returned by each provider before merging.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `provider` | `VARCHAR NOT NULL` | Source provider |
| `branch_index` | `INTEGER` | Which branch produced this candidate |
| `rank` | `INTEGER` | Rank within provider's result set |
| `title` | `VARCHAR` | Result title |
| `link` | `VARCHAR` | Result URL |
| `snippet` | `VARCHAR` | Result snippet/description |
| `domain` | `VARCHAR` | Extracted domain from link |
| `score` | `DOUBLE` | Provider's native relevance score |
| `published_date` | `VARCHAR` | Publication date string |
| `payload_json` | `JSON` | Full payload |

---

### 7. merged_candidates

**Table name:** `merged_candidates`
**DDL location:** `duckdb_store.py` → `_ensure_merged_candidates()`
**Write function:** `insert_merged_candidates()`
**Purpose:** Deduplicated, RRF-scored candidates after merging across providers. Each row is one unique result.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `rank` | `INTEGER` | Merged rank position |
| `title` | `VARCHAR` | Result title |
| `link` | `VARCHAR` | Result URL |
| `snippet` | `VARCHAR` | Result snippet |
| `domain` | `VARCHAR` | Extracted domain |
| `rrf_score` | `DOUBLE` | Reciprocal Rank Fusion score |
| `provider_count` | `INTEGER` | How many providers returned this result |
| `providers` | `VARCHAR[]` | Array of providers that returned this result |
| `overlap_flag` | `BOOLEAN` | Whether this result appeared in multiple providers |
| `payload_json` | `JSON` | Full payload |

---

### 8. rerank_stages

**Table name:** `rerank_stages`
**DDL location:** `duckdb_store.py` → `_ensure_rerank_stages()`
**Write function:** `insert_rerank_stages()`
**Purpose:** Metadata for each reranking stage (e.g. relevance, recency, entity overlap, LLM rerank).

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `stage` | `VARCHAR NOT NULL` | Stage name (relevance, recency, entity, llm_rerank, etc.) |
| `provider` | `VARCHAR` | Rerank provider if external |
| `model` | `VARCHAR` | Rerank model if LLM-based |
| `input_count` | `INTEGER` | Candidates input to this stage |
| `output_count` | `INTEGER` | Candidates output from this stage |
| `duration_ms` | `DOUBLE` | Stage execution time |
| `max_score` | `DOUBLE` | Maximum score assigned in this stage |
| `avg_score` | `DOUBLE` | Average score assigned in this stage |
| `score_threshold` | `DOUBLE` | Threshold applied to filter candidates |
| `instruction_present` | `BOOLEAN` | Whether custom rerank instruction was provided |
| `instruction_length` | `INTEGER` | Length of rerank instruction in characters |
| `query_type_hint` | `VARCHAR` | Query type hint used for rerank |
| `entity_overlap_enabled` | `BOOLEAN` | Whether entity overlap scoring was active |
| `payload_json` | `JSON` | Full payload |

---

### 9. rerank_candidates

**Table name:** `rerank_candidates`
**DDL location:** `duckdb_store.py` → `_ensure_rerank_candidates()`
**Write function:** `insert_rerank_candidates()`
**Purpose:** Per-candidate scores before and after each reranking stage. Tracks how individual results move through reranking.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `stage` | `VARCHAR NOT NULL` | Rerank stage name |
| `link` | `VARCHAR NOT NULL` | Result URL (identifies candidate) |
| `rank_before` | `INTEGER` | Rank before this stage |
| `rank_after` | `INTEGER` | Rank after this stage |
| `score_before` | `DOUBLE` | Score before this stage |
| `score_after` | `DOUBLE` | Score after this stage |
| `score_after_relevance` | `DOUBLE` | Relevance component of score after |
| `score_after_recency` | `DOUBLE` | Recency component of score after |
| `score_after_entity` | `DOUBLE` | Entity overlap component of score after |
| `recency_boost` | `DOUBLE` | Recency boost multiplier applied |
| `entity_overlap_score` | `DOUBLE` | Entity overlap similarity score |
| `diversity_removed` | `BOOLEAN` | Whether this candidate was removed by diversity filter |
| `payload_json` | `JSON` | Full payload |

---

### 10. final_results

**Table name:** `final_results`
**DDL location:** `duckdb_store.py` → `_ensure_final_results()`
**Write function:** `insert_final_results()`
**Purpose:** The final result list returned to the user after all reranking stages.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `rank` | `INTEGER` | Final rank position |
| `title` | `VARCHAR` | Result title |
| `link` | `VARCHAR` | Result URL |
| `snippet` | `VARCHAR` | Result snippet |
| `domain` | `VARCHAR` | Extracted domain |
| `final_score` | `DOUBLE` | Final composite score |
| `providers` | `VARCHAR[]` | Providers that contributed this result |
| `provider_count` | `INTEGER` | Number of contributing providers |
| `entities_count` | `INTEGER` | Number of entities matched in this result |
| `payload_json` | `JSON` | Full payload |

---

## Quality & Judge Tables

### 11. search_quality_scores

**Table name:** `search_quality_scores`
**DDL location:** `duckdb_store.py` → `_ensure_search_quality_scores()`
**Write function:** `insert_search_quality_scores()` (with `ON CONFLICT DO NOTHING`)
**Compute logic:** `quality_metrics.py` → `compute_search_quality()`
**Purpose:** Per-run derived quality metrics computed by querying pipeline tables after a run completes.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `provider_overlap_rate` | `DOUBLE` | Fraction of merged_candidates where `overlap_flag = true` |
| `domain_diversity_count` | `INTEGER` | Distinct domains in final_results |
| `domain_diversity_ratio` | `DOUBLE` | `domain_diversity_count / total_final_results` |
| `rerank_compression_ratio` | `DOUBLE` | `SUM(input_count) / SUM(output_count)` across rerank_stages |
| `avg_rrf_score` | `DOUBLE` | Average RRF score from merged_candidates |
| `top_score` | `DOUBLE` | Max `score_after` from rerank_candidates |
| `p95_score` | `DOUBLE` | Approx 95th percentile of `score_after` from rerank_candidates |
| `rewrite_variant_count` | `INTEGER` | Number of query_rewrites for this run |
| `provider_count` | `INTEGER` | Distinct providers used |
| `branch_count` | `INTEGER` | Alias for `rewrite_variant_count` |
| `total_candidates_input` | `INTEGER` | `SUM(num_results_returned)` from provider_calls |
| `total_candidates_merged` | `INTEGER` | `COUNT(*)` from merged_candidates |
| `total_candidates_reranked` | `INTEGER` | `SUM(output_count)` from rerank_stages |
| `total_final_results` | `INTEGER` | `COUNT(*)` from final_results |
| `payload_json` | `JSON` | Full payload |

---

### 12. judge_evaluations

**Table name:** `judge_evaluations`
**DDL location:** `duckdb_store.py` → `_ensure_judge_evaluations()`
**Write function:** `insert_judge_evaluation()`
**Purpose:** LLM-as-judge quality scores for search results. Fire-and-forget via `judge_runner.py`.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `tool_name` | `VARCHAR` | Tool being evaluated |
| `judge_model` | `VARCHAR` | LLM model acting as judge |
| `relevance_score` | `DOUBLE` | Relevance of results to query (0–1) |
| `accuracy_score` | `DOUBLE` | Factual accuracy of results (0–1) |
| `completeness_score` | `DOUBLE` | Completeness of coverage (0–1) |
| `source_quality_score` | `DOUBLE` | Quality/authority of sources (0–1) |
| `overall_score` | `DOUBLE` | Composite overall quality score (0–1) |
| `rationale` | `VARCHAR` | LLM rationale for scores |
| `duration_ms` | `DOUBLE` | Judge LLM call latency |
| `tokens_used` | `INTEGER` | Token consumption |
| `cost_usd` | `DOUBLE` | Estimated cost |
| `payload_json` | `JSON` | Full payload (includes `scores_raw`, `result_count`, or `error`) |

---

## Summary Tables

Materialised daily aggregates refreshed by `summaries.py` → `refresh_summary_tables()`. All process only the last 2 days of data. Use `ON CONFLICT ... DO UPDATE` for idempotent refreshes.

### 13. summary_provider_daily

**Table name:** `summary_provider_daily`
**Composite PK:** `(day, provider)`
**Source:** `provider_calls`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PK |
| `provider` | `VARCHAR NOT NULL` | PK |
| `query_count` | `BIGINT` | Number of provider calls |
| `avg_results_returned` | `DOUBLE` | Average results per call |
| `p50_results_returned` | `DOUBLE` | Median results per call |
| `avg_latency_ms` | `DOUBLE` | Average latency |
| `p50_latency_ms` | `DOUBLE` | Median latency |
| `p95_latency_ms` | `DOUBLE` | P95 latency |
| `error_rate` | `DOUBLE` | Fraction of calls with errors |
| `distinct_queries` | `BIGINT` | Distinct run_keys |

### 14. summary_intent_daily

**Table name:** `summary_intent_daily`
**Composite PK:** `(day, intent)`
**Source:** `query_understanding` + `query_rewrites`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PK |
| `intent` | `VARCHAR NOT NULL` | PK |
| `query_count` | `BIGINT` | Number of queries |
| `avg_confidence` | `DOUBLE` | Average classification confidence |
| `decomposition_rate` | `DOUBLE` | Fraction where `should_decompose = true` |
| `fallback_rate` | `DOUBLE` | Fraction where `fallback_used = true` |
| `avg_rewrite_variants` | `DOUBLE` | Average number of rewrite variants per query |

### 15. summary_rerank_daily

**Table name:** `summary_rerank_daily`
**Composite PK:** `(day, stage, provider)`
**Source:** `rerank_stages`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PK |
| `stage` | `VARCHAR NOT NULL` | PK |
| `provider` | `VARCHAR` | PK (nullable, e.g. local stages have no provider) |
| `runs_count` | `BIGINT` | Number of rerank stage invocations |
| `avg_compression_ratio` | `DOUBLE` | Average `input_count / output_count` |
| `avg_max_score` | `DOUBLE` | Average max_score across stages |
| `p50_latency_ms` | `DOUBLE` | Median stage latency |
| `p95_latency_ms` | `DOUBLE` | P95 stage latency |
| `entity_overlap_runs` | `BIGINT` | Number of runs where entity overlap was enabled |

### 16. summary_quality_daily

**Table name:** `summary_quality_daily`
**PK:** `day` (single-column)
**Source:** `search_quality_scores`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PRIMARY KEY |
| `avg_overlap_rate` | `DOUBLE` | Average provider_overlap_rate |
| `avg_domain_diversity` | `DOUBLE` | Average domain_diversity_count |
| `avg_domain_diversity_ratio` | `DOUBLE` | Average domain_diversity_ratio |
| `avg_compression_ratio` | `DOUBLE` | Average rerank_compression_ratio |
| `avg_top_score` | `DOUBLE` | Average top_score |

---

## A/B Testing Tables

### 17. ab_experiments

**Table name:** `ab_experiments`
**DDL location:** `duckdb_store.py` → `_ensure_ab_experiments()`
**Purpose:** A/B experiment definitions — one row per experiment.

| Column | Type | Notes |
|---|---|---|
| `experiment_id` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `created_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `layer` | `VARCHAR NOT NULL` | Pipeline layer (query_understanding, reranking, provider_weights) |
| `variant_a` | `VARCHAR NOT NULL` | Control variant name |
| `variant_b` | `VARCHAR NOT NULL` | Treatment variant name |
| `allocation_rate` | `DOUBLE NOT NULL` | `DEFAULT 0.5` — fraction of traffic assigned to variant_b |
| `status` | `VARCHAR NOT NULL` | `DEFAULT 'active'` — active, paused, concluded |
| `start_date` | `DATE` | When experiment started |
| `end_date` | `DATE` | When experiment concluded |
| `min_sample_size` | `INTEGER` | Minimum sample size for statistical significance |
| `payload_json` | `JSON` | Full payload |

### 18. ab_shadow_runs

**Table name:** `ab_shadow_runs`
**DDL location:** `duckdb_store.py` → `_ensure_ab_shadow_runs()`
**Write function:** `insert_ab_shadow_run()`
**Purpose:** Fire-and-forget shadow execution results. Records how the treatment variant performed compared to control.

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `experiment_id` | `VARCHAR NOT NULL` | FK → `ab_experiments.experiment_id` |
| `variant` | `VARCHAR NOT NULL` | Variant key that was shadowed |
| `layer` | `VARCHAR NOT NULL` | Pipeline layer |
| `duration_ms` | `DOUBLE` | Shadow execution duration |
| `judge_score` | `DOUBLE` | Judge quality score for shadow results |
| `tokens_used` | `INTEGER` | Token consumption |
| `cost_usd` | `DOUBLE` | Estimated cost |
| `error_type` | `VARCHAR` | Error type if shadow failed |
| `payload_json` | `JSON` | Includes `control_duration_ms`, `latency_delta_ms`, summaries |

### 19. ab_experiment_variants

**Table name:** `ab_experiment_variants`
**DDL location:** `duckdb_store.py` → `_ensure_ab_experiment_variants()`
**Purpose:** Detailed variant definitions with configuration.

| Column | Type | Notes |
|---|---|---|
| `variant_id` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `experiment_id` | `VARCHAR NOT NULL` | FK → `ab_experiments.experiment_id` |
| `variant_name` | `VARCHAR NOT NULL` | Human-readable variant name |
| `description` | `VARCHAR` | Description of what this variant changes |
| `config_json` | `JSON` | Configuration overrides for this variant |
| `created_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |

### 20. ab_assignments

**Table name:** `ab_assignments`
**DDL location:** `duckdb_store.py` → `_ensure_ab_assignments()`
**Purpose:** Sticky run-to-variant assignments. Records which variant each run_key was assigned to.

| Column | Type | Notes |
|---|---|---|
| `assignment_id` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `experiment_id` | `VARCHAR NOT NULL` | FK → `ab_experiments.experiment_id` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `variant` | `VARCHAR NOT NULL` | Assigned variant |
| `assigned_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `payload_json` | `JSON` | Full payload |

### 21. ab_results

**Table name:** `ab_results`
**DDL location:** `duckdb_store.py` → `_ensure_ab_results()`
**Purpose:** Measured outcomes for each assigned run. Primary and secondary metrics for statistical analysis.

| Column | Type | Notes |
|---|---|---|
| `result_id` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `experiment_id` | `VARCHAR NOT NULL` | FK → `ab_experiments.experiment_id` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `variant` | `VARCHAR NOT NULL` | Variant used |
| `primary_metric` | `DOUBLE` | Primary outcome metric |
| `secondary_metric` | `DOUBLE` | Secondary outcome metric |
| `duration_ms` | `DOUBLE` | Execution duration |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `payload_json` | `JSON` | Full payload |

---

## Views

All views are created idempotently via `CREATE OR REPLACE VIEW` in `views.py` → `ensure_views()`. There are 13 views.

### v_search_run_story

**Purpose:** Per-run join across all pipeline tables — one row per `run_key` with counts and summary metrics from every pipeline stage.

**Columns provided:** `run_key`, `query`, `normalized_query`, `research_goal`, `status`, `total_duration_ms`, `candidate_count`, `rewrite_enabled`, `run_recorded_at`, `rewrite_variant_count`, `provider_call_count`, `provider_candidate_count`, `merged_candidate_count`, `final_result_count`, `avg_provider_latency_ms`, `avg_rerank_latency_ms`, `intent`, `confidence`, `overlap_rate`, `domain_diversity`

**Tables joined:** `search_runs` + `query_understanding` + `query_rewrites`(agg) + `provider_calls`(agg) + `provider_candidates`(agg) + `merged_candidates`(agg) + `final_results`(agg) + `rerank_stages`(agg) + `search_quality_scores`

### v_provider_survival_funnel

**Purpose:** Per-provider funnel analysis — how many candidates each provider contributes, how many survive merging, and how many make it to final results.

**Key metric:** `survival_rate_pct` — percentage of provider's candidates that reach final_results.

**Tables joined:** `provider_candidates` + `merged_candidates` + `final_results`

### v_rewrite_effectiveness

**Purpose:** Per-run summary of query rewriting effectiveness — variant count, providers used, distinct candidates gathered, final result count.

**Tables joined:** `search_runs` + `query_rewrites` + `provider_calls` + `provider_candidates`

### v_rerank_stage_performance

**Purpose:** Aggregate rerank stage performance over all time by `(stage, provider, model)`.

**Metrics:** `runs`, `avg_input_count`, `avg_output_count`, `avg_duration_ms`, `avg_max_score`, `avg_avg_score`, `entity_overlap_runs`

**Source:** `rerank_stages`

### v_query_classification_distribution

**Purpose:** Daily query intent distribution over the last 30 days.

**Metrics:** `day`, `intent`, `count`, `avg_confidence`, `fallback_count`, `decomposed_count`

**Source:** `query_understanding` (filtered to last 30 days)

### v_provider_quality_trend

**Purpose:** Daily provider call quality metrics over the last 30 days.

**Metrics:** `day`, `provider`, `calls`, `avg_results`, `avg_latency_ms`, `p50_latency_ms`, `p95_latency_ms`, `error_rate`, `distinct_runs`

**Source:** `provider_calls` (filtered to last 30 days)

### v_rerank_stage_impact

**Purpose:** Per-run rerank stage detail with derived metrics.

**Derived columns:** `compression_ratio` (= `input_count / output_count`), `avg_score_delta` (= `AVG(score_after - score_before)`), `diversity_removed_count`

**Tables joined:** `rerank_stages` + `rerank_candidates`

### v_daily_quality_summary

**Purpose:** Daily aggregate of search quality metrics over all time.

**Metrics:** `day`, `query_count`, `avg_total_latency_ms`, `avg_overlap_rate`, `avg_domain_diversity`, `avg_compression_ratio`, `avg_top_score`, `avg_judge_score`

**Tables joined:** `search_runs` + `search_quality_scores` + `judge_evaluations`

### v_judge_score_distribution

**Purpose:** Judge model score distribution over the last 30 days by `(tool_name, judge_model)`.

**Metrics:** `evaluations`, `avg_relevance`, `avg_accuracy`, `avg_completeness`, `avg_source_quality`, `avg_overall`, `p50_overall`, `p95_overall`

**Source:** `judge_evaluations` (filtered to last 30 days)

### v_judge_trend

**Purpose:** Daily judge evaluation trend over the last 30 days.

**Metrics:** `day`, `tool_name`, `evaluations`, `avg_overall`, `avg_judge_latency_ms`, `avg_tokens`

**Source:** `judge_evaluations` (filtered to last 30 days)

### v_ab_experiment_summary

**Purpose:** Complete A/B experiment overview — joins experiments, variants, assignments, and results into one row per experiment.

**Metrics:** `experiment_id`, `layer`, `status`, `variant_a`, `variant_b`, `allocation_rate`, `min_sample_size`, `variant_count`, `assignment_count`, `unique_run_count`, `avg_primary_metric`, `avg_secondary_metric`, `avg_duration_ms`, `result_count`

**Tables joined:** `ab_experiments` + `ab_experiment_variants`(agg) + `ab_assignments`(agg) + `ab_results`(agg)

### v_ab_variant_comparison

**Purpose:** Per-variant metrics with STDDEV and control/treatment labelling for statistical comparison.

**Key columns:** `variant_role` (`'control'` / `'treatment'` / `'other'`), `avg_primary_metric`, `avg_secondary_metric`, `stddev_primary_metric`, `run_count`, `result_count`

**Tables joined:** `ab_experiments` + `ab_results`

### v_ab_shadow_run_analysis

**Purpose:** Shadow run analysis with windowed metrics — compares each shadow run against its variant's average.

**Windowed columns:** `variant_avg_latency_ms`, `latency_delta_ms` (= `duration_ms - variant_avg`), `variant_avg_judge_score`, `judge_score_delta` (= `judge_score - variant_avg`)

**Tables joined:** `ab_shadow_runs` + `ab_experiments`

---

## Module Map

| File | Purpose |
|---|---|
| `analytics/duckdb_store.py` | All table DDL (`_ensure_*` functions), insert functions, `append_event()`, `BatchWriter` |
| `analytics/views.py` | `ensure_views()` — 13 `CREATE OR REPLACE VIEW` statements in `VIEW_DEFINITIONS` dict |
| `analytics/quality_metrics.py` | `compute_search_quality()` — queries pipeline tables for a `run_key`, inserts into `search_quality_scores` |
| `analytics/summaries.py` | `refresh_summary_tables()` — idempotent daily aggregation into 4 summary tables |
| `analytics/judge_prompt.py` | Judge system prompt, `build_judge_user_prompt()`, `parse_judge_response()` |
| `analytics/judge_runner.py` | `run_judge_evaluation()` — fire-and-forget LLM judge call, inserts into `judge_evaluations` |
| `analytics/judge_calibration.py` | Judge score calibration utilities |
| `ab_testing/models.py` | `ABExperiment`, `ABVariant`, `Assignment` dataclasses |
| `ab_testing/assignment.py` | `get_assigned_variant()` — deterministic hash-based bucket allocation |
| `ab_testing/yaml_loader.py` | `load_experiments()`, `save_experiments()` — YAML ↔ ABExperiment |
| `ab_testing/wiring.py` | `get_ab_overrides()` — glue between YAML experiments and pipeline stages |
| `ab_testing/shadow_runner.py` | `run_shadow()` — fire-and-forget shadow execution with DuckDB persistence |

---

## Settings Reference

| Setting | Default | Description |
|---|---|---|
| `KINDLY_ANALYTICS_ENABLED` | `true` | Global analytics toggle — all inserts/views skip when `false` |
| `KINDLY_AB_TESTING_ENABLED` | `false` | A/B testing toggle |
| `KINDLY_AB_CONFIG_PATH` | `.kindly/experiments.yaml` | Experiment YAML config file path |
| `KINDLY_AB_SHADOW_MODE_DEFAULT` | `true` | Default shadow mode for A/B variants |
| `KINDLY_AB_ASSIGNMENT_CACHE_TTL_SECONDS` | `300` | Sticky assignment cache TTL |
| `KINDLY_JUDGE_EVALUATION_ENABLED` | `false` | LLM judge evaluation toggle |
| `KINDLY_JUDGE_MODEL` | `google/gemini-2.0-flash-001` | Judge LLM model |
| `KINDLY_JUDGE_TIMEOUT_SECONDS` | `10.0` | Judge LLM call timeout |

---

## A/B Pipeline Wiring

A/B overrides are wired into 3 pipeline layers via `get_ab_overrides(run_key, layer)`:

1. **`query_understanding`** — overrides LLM model, prompt variant, decomposition settings
2. **`reranking`** — overrides rerank provider, top_k, diversity_weight
3. **`provider_weights`** — overrides per-provider RRF weights

### Shadow Mode

When a variant has `shadow: True` in its config, the override is applied as a **background task** (fire-and-forget via `asyncio.create_task` in `shadow_runner.py`), not on the production path. The production path uses the control configuration; the variant runs invisibly alongside and results are recorded in `ab_shadow_runs`.

### CLI

Managed via `web-search-cli experiments` subcommands:

| Command | Description |
|---|---|
| `list` | List all experiments |
| `enable` | Enable/start an experiment |
| `disable` | Disable/pause an experiment |
| `conclude` | Conclude an experiment with a winner |
| `stats` | Show experiment statistics |
| `create` | Create a new experiment |

---

## ER Relationships

```
search_runs (1) ──── (N) query_understanding
search_runs (1) ──── (N) query_rewrites
search_runs (1) ──── (N) provider_calls
search_runs (1) ──── (N) provider_candidates
search_runs (1) ──── (N) merged_candidates
search_runs (1) ──── (N) rerank_stages
search_runs (1) ──── (N) rerank_candidates
search_runs (1) ──── (N) final_results
search_runs (1) ──── (1) search_quality_scores        [PK = run_key]
search_runs (1) ──── (N) judge_evaluations
search_runs (1) ──── (N) ab_assignments
search_runs (1) ──── (N) ab_results
search_runs (1) ──── (N) ab_shadow_runs

ab_experiments (1) ──── (N) ab_experiment_variants
ab_experiments (1) ──── (N) ab_assignments
ab_experiments (1) ──── (N) ab_results
ab_experiments (1) ──── (N) ab_shadow_runs

rerank_stages (1) ──── (N) rerank_candidates          [via run_key + stage]
```

All relationships are joined via `run_key` (a `VARCHAR` unique per pipeline invocation) unless otherwise noted.

---

*Generated from source: `duckdb_store.py`, `views.py`, `quality_metrics.py`, `summaries.py`, `judge_runner.py`, `assignment.py`, `wiring.py`, `shadow_runner.py`, `models.py`*