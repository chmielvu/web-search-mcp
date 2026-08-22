# DuckDB Schema Reference

> Full schema documentation for the web-search-mcp analytics and A/B testing database.

**9 fact/embedding tables + provider_health + quality + judge + result_labels + 4 summary tables + A/B tables** · **10 dashboard views + A/B views + eval views**

---

## Table of Contents

1. [Search Pipeline Tables](#search-pipeline-tables)
2. [Embedding Tables](#embedding-tables)
3. [Quality & Judge Tables](#quality--judge-tables)
4. [Provider Health](#provider-health)
5. [Summary Tables](#summary-tables)
6. [A/B Testing Tables](#ab-testing-tables)
7. [Views](#views)
8. [Branch-Role Model](#branch-role-model)

---

## Search Pipeline Tables

These tables capture the lifecycle of a single web-search run through the
fixed six-branch topology: run start → branch execution → provider calls →
merge → rerank → final results.

### 1. search_runs

**Table name:** `search_runs`
**DDL location:** `analytics/writers/schema.py` → `_ensure_search_runs()`
**Write function:** `insert_search_run()`
**Purpose:** One row per search pipeline invocation. Root entity for the
entire run lifecycle.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | Unique run identifier |
| `tool_call_id` | `VARCHAR` | MCP tool call ID |
| `session_id` | `VARCHAR` | User session identifier |
| `query` | `VARCHAR NOT NULL` | Original user query |
| `normalized_query` | `VARCHAR` | Normalised query from plan |
| `research_goal` | `VARCHAR` | Research goal from request |
| `intent` | `VARCHAR` | Classified intent |
| `understanding_confidence` | `DOUBLE` | LLM confidence in classification |
| `num_results_requested` | `INTEGER` | How many results the caller requested |
| `rewrite_enabled` | `BOOLEAN` | Whether query rewriting was active |
| `result_offset` | `INTEGER` | Pagination offset |
| `selected_providers` | `VARCHAR[]` | Union of all branch provider lists |
| `skipped_providers` | `VARCHAR[]` | Providers skipped (cooldown/disabled) |
| `branch_count` | `INTEGER` | Number of branches (normally 6) |
| `provider_count` | `INTEGER` | Distinct providers that returned results |
| `merged_count` | `INTEGER` | Unique candidates after RRF merge |
| `reranked_count` | `INTEGER` | Candidates after reranking |
| `final_result_count` | `INTEGER` | Results returned to user |
| `candidate_count` | `INTEGER` | Total candidates in ranked pool |
| `has_more` | `BOOLEAN` | Whether more results exist beyond returned set |
| `status` | `VARCHAR` | Run status (success, error, partial) |
| `error_type` | `VARCHAR` | Error classification if run failed |
| `duration_ms` | `DOUBLE` | Total pipeline duration |
| `reranker_provider` | `VARCHAR` | Rerank provider used |
| `reranker_model` | `VARCHAR` | Rerank model used |
| `rake_terms` | `VARCHAR[]` | RAKE-extracted support terms |
| `brave_autosuggest` | `VARCHAR[]` | Brave autosuggest suggestions |
| `brave_spellcheck` | `VARCHAR` | Brave spellcheck correction |
| `rewrite_prompt` | `VARCHAR` | LLM rewrite prompt |
| `rewrite_model` | `VARCHAR` | LLM model used for rewrite |
| `rewrite_input_tokens` | `INTEGER` | Rewrite input tokens |
| `rewrite_output_tokens` | `INTEGER` | Rewrite output tokens |
| `rewrite_latency_ms` | `DOUBLE` | Rewrite latency |
| `rewrite_error` | `VARCHAR` | Rewrite error type if failed |
| `payload_json` | `JSON` | Full run payload |

#### Indexes

| Index Name | Columns |
|---|---|
| `idx_runs_run_key` | `run_key` |
| `idx_runs_recorded_at` | `recorded_at` |

---

### 2. search_branches

**Table name:** `search_branches`
**DDL location:** `analytics/writers/schema.py` → `_ensure_search_branches()`
**Write function:** `insert_search_branches()`
**Purpose:** One row per branch per run. Captures the fixed six-branch
topology with explicit provider allowlists.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `branch_index` | `INTEGER NOT NULL` | 0–5 branch position |
| `branch_role` | `VARCHAR NOT NULL` | One of the six fixed roles |
| `branch_query` | `VARCHAR NOT NULL` | Query text for this branch |
| `branch_why` | `VARCHAR` | Static role description |
| `support_terms` | `VARCHAR[]` | RAKE-extracted support terms |
| `max_results` | `INTEGER` | Per-provider request depth |
| `assigned_providers` | `VARCHAR[]` | Explicit branch allowlist |
| `attempted_providers` | `VARCHAR[]` | Providers that executed |
| `skipped_providers` | `VARCHAR[]` | Providers skipped (cooldown/disabled) |
| `results_count` | `INTEGER` | Deduplicated results from this branch |
| `latency_ms` | `DOUBLE` | Branch wall-clock latency |
| `payload_json` | `JSON` | Full payload |

---

### 3. provider_calls

**Table name:** `provider_calls`
**DDL location:** `analytics/writers/schema.py` → `_ensure_provider_calls()`
**Write function:** `insert_provider_calls()`
**Purpose:** Every outbound API call to a search provider, attributed to a
branch role.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `branch_index` | `INTEGER` | Which branch this call belongs to |
| `branch_role` | `VARCHAR` | Branch role for this call |
| `provider` | `VARCHAR NOT NULL` | Provider name |
| `branch_query` | `VARCHAR` | The query sent to this provider |
| `status` | `VARCHAR NOT NULL` | success / error / timeout / skipped |
| `num_results_requested` | `INTEGER` | Results requested |
| `num_results_returned` | `INTEGER` | Results actually returned |
| `latency_ms` | `DOUBLE` | Provider API latency |
| `error_type` | `VARCHAR` | Error type if call failed |
| `error_message` | `VARCHAR` | Error message if call failed |
| `candidate_urls` | `VARCHAR[]` | URLs returned (capped at 32) |
| `payload_json` | `JSON` | Full payload |

---

### 4. search_candidates

**Table name:** `search_candidates`
**DDL location:** `analytics/writers/schema.py` → `_ensure_search_candidates()`
**Write function:** `insert_search_candidates()`
**Purpose:** Deduplicated, RRF-scored candidates after merging across all
branches and providers. Each row is one unique result URL per run.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `link` | `VARCHAR NOT NULL` | Result URL |
| `title` | `VARCHAR` | Result title |
| `snippet` | `VARCHAR` | Result snippet |
| `domain` | `VARCHAR` | Extracted domain from link |
| `rrf_score` | `DOUBLE` | Reciprocal Rank Fusion score |
| `provider_count` | `INTEGER` | How many providers returned this result |
| `providers` | `VARCHAR[]` | Array of providers that returned this result |
| `overlap_flag` | `BOOLEAN` | Whether this result appeared in multiple providers |
| `payload_json` | `JSON` | Full payload |

---

### 5. rerank_stages

**Table name:** `rerank_stages`
**DDL location:** `analytics/writers/schema.py` → `_ensure_rerank_stages()`
**Write function:** `insert_rerank_stages()`
**Purpose:** Metadata for each reranking stage (bi_encoder, cross-encoder,
llm_rerank, diversity, rerank.final).

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `stage` | `VARCHAR NOT NULL` | Stage name |
| `provider` | `VARCHAR` | Rerank provider (NULL for internal stages) |
| `model` | `VARCHAR` | Rerank model |
| `input_count` | `INTEGER` | Candidates input to this stage |
| `output_count` | `INTEGER` | Candidates output from this stage |
| `duration_ms` | `DOUBLE` | Stage execution time |
| `max_score` | `DOUBLE` | Maximum score assigned in this stage |
| `avg_score` | `DOUBLE` | Average score assigned in this stage |
| `score_threshold` | `DOUBLE` | Threshold applied to filter candidates |
| `alpha_blend` | `DOUBLE` | Alpha blend weight if applicable |
| `input_tokens` | `INTEGER` | Input tokens consumed |
| `output_tokens` | `INTEGER` | Output tokens consumed |
| `status` | `VARCHAR` | Stage status |
| `error_type` | `VARCHAR` | Error type if stage failed |
| `instruction_present` | `BOOLEAN` | Whether custom rerank instruction was provided |
| `instruction_length` | `INTEGER` | Length of rerank instruction in characters |
| `query_type_hint` | `VARCHAR` | Query type hint used for rerank |
| `entity_overlap_enabled` | `BOOLEAN` | Whether entity overlap scoring was active |
| `payload_json` | `JSON` | Full payload |

---

### 6. rerank_candidates

**Table name:** `rerank_candidates`
**DDL location:** `analytics/writers/schema.py` → `_ensure_rerank_candidates()`
**Write function:** `insert_rerank_candidates()`
**Purpose:** Per-candidate scores before and after each reranking stage.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `stage` | `VARCHAR NOT NULL` | Rerank stage name |
| `link` | `VARCHAR NOT NULL` | Result URL |
| `candidate_id` | `VARCHAR` | Content-based candidate ID |
| `canonical_result_id` | `VARCHAR` | Canonical URL ID |
| `rank_before` | `INTEGER` | Rank before this stage |
| `rank_after` | `INTEGER` | Rank after this stage |
| `score_before` | `DOUBLE` | Score before this stage |
| `score_after` | `DOUBLE` | Score after this stage |
| `bm25_score` | `DOUBLE` | BM25 sparse score |
| `bm25_rank` | `INTEGER` | BM25 rank |
| `dense_score` | `DOUBLE` | Dense vector score |
| `dense_rank` | `INTEGER` | Dense rank |
| `cross_encoder_raw` | `DOUBLE` | Cross-encoder raw score |
| `llm_raw_score` | `DOUBLE` | LLM raw score |
| `fused_score` | `DOUBLE` | Fused composite score |
| `hybrid_rrf_score` | `DOUBLE` | Hybrid RRF score |
| `recency_boost` | `DOUBLE` | Recency boost multiplier |
| `entity_overlap_score` | `DOUBLE` | Entity overlap similarity score |
| `diversity_removed` | `BOOLEAN` | Removed by diversity filter |
| `payload_json` | `JSON` | Full payload |

---

### 7. final_results

**Table name:** `final_results`
**DDL location:** `analytics/writers/schema.py` → `_ensure_final_results()`
**Write function:** `insert_final_results()`
**Purpose:** The final result list returned to the user after all reranking.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `rank` | `INTEGER` | Final rank position |
| `title` | `VARCHAR` | Result title |
| `link` | `VARCHAR` | Result URL |
| `snippet` | `VARCHAR` | Result snippet |
| `domain` | `VARCHAR` | Extracted domain |
| `final_score` | `DOUBLE` | Final composite score |
| `providers` | `VARCHAR[]` | Providers that contributed this result |
| `provider_count` | `INTEGER` | Number of contributing providers |
| `entities_count` | `INTEGER` | Number of entities matched |
| `candidate_id` | `VARCHAR` | Content-based candidate ID |
| `canonical_result_id` | `VARCHAR` | Canonical URL ID |
| `payload_json` | `JSON` | Full payload |

---

## Embedding Tables

### 8. query_embeddings

**Table name:** `query_embeddings`
**DDL location:** `analytics/writers/schema.py` → `_ensure_query_embeddings()`
**Write function:** `insert_query_embeddings()`
**Purpose:** One row per run storing the query embedding for vss similarity
search.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `embedding` | `FLOAT[786]` | Query embedding vector |
| `model_id` | `VARCHAR` | Embedding model ID |
| `payload_json` | `JSON` | Full payload |

#### Indexes

| Index Name | Columns |
|---|---|
| `idx_qemb_run_key` | `run_key` |
| `idx_qemb_hnsw` | `embedding` (HNSW, if vss available) |

---

### 9. candidate_embeddings

**Table name:** `candidate_embeddings`
**DDL location:** `analytics/writers/schema.py` → `_ensure_candidate_embeddings()`
**Write function:** `insert_candidate_embeddings()`
**Purpose:** One row per candidate per run storing the candidate embedding
for vss similarity search.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `link` | `VARCHAR NOT NULL` | Result URL |
| `title` | `VARCHAR` | Result title (first line) |
| `embedding` | `FLOAT[786]` | Candidate embedding vector |
| `model_id` | `VARCHAR` | Embedding model ID |
| `payload_json` | `JSON` | Full payload |

#### Indexes

| Index Name | Columns |
|---|---|
| `idx_cemb_run_key` | `run_key` |
| `idx_cemb_hnsw` | `embedding` (HNSW, if vss available) |

---

## Quality & Judge Tables

### 10. search_quality_scores

**Table name:** `search_quality_scores`
**DDL location:** `analytics/writers/schema.py` → `_ensure_search_quality_scores()`
**Write function:** `insert_search_quality_scores()` (with `ON CONFLICT DO NOTHING`)
**Compute logic:** `analytics/quality_metrics.py` → `compute_search_quality()`
**Purpose:** Per-run derived quality metrics computed from the live pipeline
tables.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | **PRIMARY KEY** |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `provider_overlap_rate` | `DOUBLE` | Fraction of `search_candidates` with overlap |
| `domain_diversity_count` | `INTEGER` | Distinct domains in `final_results` |
| `domain_diversity_ratio` | `DOUBLE` | `domain_diversity_count / total_final_results` |
| `rerank_compression_ratio` | `DOUBLE` | `SUM(input_count) / SUM(output_count)` from `rerank_stages` |
| `avg_rrf_score` | `DOUBLE` | Average RRF score from `search_candidates` |
| `top_score` | `DOUBLE` | Max `score_after` from `rerank_candidates` |
| `p95_score` | `DOUBLE` | Approx 95th percentile of `score_after` |
| `provider_count` | `INTEGER` | Distinct providers from `provider_calls` |
| `branch_count` | `INTEGER` | Count from `search_branches` (normally 6) |
| `total_candidates_input` | `INTEGER` | `SUM(num_results_returned)` from `provider_calls` |
| `total_candidates_merged` | `INTEGER` | `COUNT(*)` from `search_candidates` |
| `total_candidates_reranked` | `INTEGER` | `SUM(output_count)` from `rerank_stages` |
| `total_final_results` | `INTEGER` | `COUNT(*)` from `final_results` |
| `ndcg_at_10` | `DOUBLE` | NDCG@10 if judge scores available |
| `payload_json` | `JSON` | Full payload |

---

### 11. judge_evaluations

**Table name:** `judge_evaluations`
**DDL location:** `analytics/writers/schema.py` → `_ensure_judge_evaluations()`
**Write function:** `insert_judge_evaluation()`
**Purpose:** 4D LLM-as-judge quality scores for search results.
Fire-and-forget via `judge_runner.py`.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `run_key` | `VARCHAR NOT NULL` | FK → `search_runs.run_key` |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `evaluated_at` | `TIMESTAMPTZ NOT NULL` | `DEFAULT now()` |
| `tool_name` | `VARCHAR` | Tool being evaluated |
| `judge_model` | `VARCHAR` | LLM model acting as judge |
| `model_used` | `VARCHAR` | Actual model used |
| `link` | `VARCHAR` | Result URL being judged |
| `relevance_grade` | `VARCHAR` | Relevance grade |
| `relevance_score` | `DOUBLE` | Relevance score (0–1) |
| `accuracy_grade` | `VARCHAR` | Accuracy grade |
| `accuracy_score` | `DOUBLE` | Accuracy score (0–1) |
| `completeness_grade` | `VARCHAR` | Completeness grade |
| `completeness_score` | `DOUBLE` | Completeness score (0–1) |
| `source_quality_grade` | `VARCHAR` | Source quality grade |
| `source_quality_score` | `DOUBLE` | Source quality score (0–1) |
| `overall_score` | `DOUBLE` | Composite overall quality score (0–1) |
| `rationale` | `VARCHAR` | LLM rationale for scores |
| `duration_ms` | `DOUBLE` | Judge LLM call latency |
| `input_tokens` | `INTEGER` | Input tokens |
| `output_tokens` | `INTEGER` | Output tokens |
| `tokens_used` | `INTEGER` | Total tokens |
| `cost_usd` | `DOUBLE` | Estimated cost |
| `payload_json` | `JSON` | Full payload |

---

### 12. result_labels

**Table name:** `result_labels`
**DDL location:** `analytics/writers/schema.py` → `_ensure_result_labels()`
**Write functions:** `insert_result_label()` / `insert_result_labels()`
**Purpose:** Offline relevance annotations for replay and positional analysis. This
table does not alter live ranking or treat model judgments as human ground truth.

#### Columns

| Column | Type | Notes |
|---|---|---|
| `label_id` | `VARCHAR NOT NULL PRIMARY KEY` | Stable idempotency key |
| `recorded_at` | `TIMESTAMPTZ NOT NULL` | Annotation timestamp |
| `run_key` | `VARCHAR NOT NULL` | Search run being labeled |
| `position` | `INTEGER NOT NULL` | Zero-based result position |
| `stage` | `VARCHAR NOT NULL` | `final` or a rerank stage |
| `label` | `DOUBLE NOT NULL` | Nonnegative graded relevance label |
| `canonical_result_id` | `VARCHAR` | Canonical URL identity when available |
| `raw_url` | `VARCHAR` | Result URL fallback |
| `source` | `VARCHAR NOT NULL` | `human`, `eval`, `llm_judge`, or import source |
| `annotator_id` | `VARCHAR` | Reviewer or model identity |
| `rubric_version` | `VARCHAR NOT NULL` | Label rubric version |
| `discounted_gain` | `DOUBLE` | `label / log2(position + 2)` |
| `notes` | `VARCHAR` | Optional rationale |
| `payload_json` | `JSON` | Additional bounded provenance |

`discounted_gain` is a descriptive logarithmic position discount. It is not
inverse-propensity correction for click position bias.

---

## Provider Health

### 13. provider_health_transitions

**Table name:** `provider_health_transitions`
**DDL location:** `analytics/writers/schema.py` → `_ensure_provider_health_transitions()`
**Purpose:** Circuit-breaker state transitions.

---

## Summary Tables

Materialised daily aggregates refreshed by `summaries.py` →
`refresh_summary_tables()`. All process only the last 2 days of data.
Use `ON CONFLICT ... DO UPDATE` for idempotent refreshes.

### 14. summary_provider_daily

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
| `error_rate` | `DOUBLE` | Fraction of calls with `error_type IS NOT NULL` |
| `distinct_queries` | `BIGINT` | Distinct run_keys |

### 15. summary_intent_daily

**Table name:** `summary_intent_daily`
**Composite PK:** `(day, intent)`
**Source:** `search_runs`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PK |
| `intent` | `VARCHAR NOT NULL` | PK |
| `query_count` | `BIGINT` | Number of queries |
| `avg_confidence` | `DOUBLE` | Average `understanding_confidence` |
| `avg_branch_count` | `DOUBLE` | Average `branch_count` (observability invariant, expect 6.0) |

### 16. summary_rerank_daily

**Table name:** `summary_rerank_daily`
**Composite PK:** `(day, stage, provider)`
**Source:** `rerank_stages`

| Column | Type | Notes |
|---|---|---|
| `day` | `DATE NOT NULL` | PK |
| `stage` | `VARCHAR NOT NULL` | PK |
| `provider` | `VARCHAR NOT NULL` | PK (NULL normalized to `'internal'`) |
| `runs_count` | `BIGINT` | Number of rerank stage invocations |
| `avg_compression_ratio` | `DOUBLE` | Average `input_count / output_count` |
| `avg_max_score` | `DOUBLE` | Average max_score across stages |
| `p50_latency_ms` | `DOUBLE` | Median stage latency |
| `p95_latency_ms` | `DOUBLE` | P95 stage latency |
| `entity_overlap_runs` | `BIGINT` | Runs where entity overlap was enabled |

### 17. summary_quality_daily

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

A/B experiment, variant, assignment, result, and shadow-run tables are
defined in `analytics/writers/ab_schema.py`. See that module for DDL.

---

## Views

10 dashboard views are defined in `analytics/views.py` →
`_build_dashboard_view_sql()`:

1. `vw_run_summary` — run-level summary with latency tiers and rewrite status
2. `vw_provider_performance` — per-provider success/latency/error aggregation
3. `vw_branch_summary` — per-branch role/query/provider/status with `support_terms`
4. `vw_candidate_funnel` — candidate survival funnel from provider → merged → rerank → final
5. `vw_rerank_timeline` — rerank stage timeline unioned with search-run rows
6. `vw_rewrite_diagnostics` — rewrite-enabled runs with RAKE/Brave/LLM metadata
7. `vw_daily_trend` — gap-free 30-day daily trend
8. `vw_quality_distribution` — judge evaluation quality tiers
9. `vw_provider_health` — provider health transitions
10. `vw_judge_quality` — 4D judge quality summary with grade labels

A/B views (`v_ab_experiment_summary`, `v_ab_variant_comparison`,
`v_ab_shadow_run_analysis`) and eval views are also created by
`ensure_views()`.

---

## Branch-Role Model

The fixed six-branch topology stores `branch_role` (not `branch_target`)
on `search_branches` and `provider_calls`. The six roles are:

| # | Branch role | Provider membership |
|---|---|---|
| 1 | `original_free` | Reachable from `("searxng", "ddg", "gemma", "degoog")` |
| 2 | `paid_brave` | `("brave",)` when reachable |
| 3 | `paid_google` | One of `("brightdata", "serper", "search_router")` round-robin |
| 4 | `paid_other` | `("brightdata_yandex", "brightdata_bing", "serpapi")` |
| 5 | `neural` | `("gemma", "qdrant", "composio_llm_search")` |
| 6 | `specialized` | Intent-policy-selected providers |

Each branch owns its `assigned_providers` tuple explicitly. `support_terms`
replaces the old `must_keep_terms`. Branch weights are removed.
`search_runs.branch_count` should normally be 6.

`summary_intent_daily.avg_branch_count` is the daily per-intent average of
`branch_count` and serves as an observability invariant: 6.0 means the
fixed topology was preserved.

---

*Generated from `analytics/writers/schema.py`, `analytics/writers/summary_schema.py`,
`analytics/writers/inserts.py`, `analytics/views.py`, `analytics/quality_metrics.py`,
`analytics/summaries.py`, `analytics/reports.py`, `analytics/writers/ab_schema.py`*
