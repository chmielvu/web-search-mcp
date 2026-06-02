# DuckDB + MotherDuck Analytics Final Design

**Date checked:** 2026-06-02T04:12:34+02:00  
**Scope:** Final cross-validated design for local DuckDB analytics, MotherDuck sync/views, Grafana dashboards, and MCP eval data for `web-search-mcp`.

This supersedes:

- `plans/duckdb/duckdb-analytics-data-audit-2026-06-01.md`
- `plans/duckdb/llm-ml-analytics-on-duckdb-research-2026-06-01.md`
- `plans/duckdb/mcp_advanced_research_insights.md`

## 1. Final Position

Keep the current architecture direction:

1. Append full-fidelity runtime events locally to `.kindly/analytics/search_events.duckdb`.
2. Sync incrementally to MotherDuck for Grafana and shared analysis.
3. Build SQL views that shred nested JSON payloads into stage-specific analytical surfaces.
4. Add missing observability events at cache, provider-health, middleware/session, content-stage, and error-classification boundaries.
5. Add MCP eval result tables that join eval outcomes to the real event timeline and candidate-survival views.

Do not make hidden internal research agents or MotherDuck-hosted community extensions the primary path. Flock/FlockMTL belongs in the local DuckDB analytics lane, not in MotherDuck-hosted views, because:

- MotherDuck currently documents unsupported custom/community extensions server-side.
- This MCP intentionally exposes search, fetch, and synthesis as separate tools so external agents control orchestration.
- Current analytics gaps still need event normalization and additional data, but NL2SQL and local LLM-in-SQL analytics are part of the target interface, not optional afterthoughts.

## 2. Evidence Used

### Workspace Evidence

- Local DuckDB exists at `.kindly/analytics/search_events.duckdb`.
- Local table count: `2213` rows.
- Top stored event types include `provider.search.result` (`366`), `tool.web_search.request` (`299`), `search.merge.summary` (`233`), `tool.web_search.response` (`223`), `search.rerank.summary` (`202`), and `query.rewrite.completed` (`155`).
- Verified schema defect: all `366` `provider.search.result` rows have fixed column `provider IS NULL` because events emit `provider_name`, while `analytics/duckdb_store.py` extracts only `provider`.

### Current Documentation Evidence

- DuckDB supports persistent file databases via `duckdb.connect("file.db")`, with data reloadable by reconnecting to the same file. DuckDB docs also recommend explicit connection objects instead of relying on global module state for package code.
- DuckDB JSON analysis should use `json_extract`, `json_extract_string`, and table functions such as `json_each` / `json_tree` for nested arrays and objects.
- MotherDuck supports `ATTACH 'md:'`, loading local files or local DuckDB databases into MotherDuck, and dual execution between local DuckDB and MotherDuck.
- MotherDuck currently documents supported DuckDB versions by region as `1.4.0` to `1.4.4` for US East and `1.4.1` to `1.4.4` for Europe. The repo currently pins `duckdb>=1.1.0,<1.5.3`; the implementation plan must tighten or validate this before sync.
- MotherDuck documents unsupported server-side custom/native UDFs and custom/community extensions. Do not depend on community extensions such as Flock inside MotherDuck views. Use Flock only against local DuckDB unless MotherDuck explicitly supports the extension later.

## 3. Current Implementation To Preserve

### Local Event Store

Current file:

- `src/kindly_web_search_mcp_server/analytics/duckdb_store.py`

Current table:

```sql
search_events(
  event_id VARCHAR,
  event_name VARCHAR,
  recorded_at TIMESTAMP,
  run_key VARCHAR,
  tool_name VARCHAR,
  phase VARCHAR,
  query VARCHAR,
  normalized_query VARCHAR,
  research_goal VARCHAR,
  provider VARCHAR,
  model VARCHAR,
  duration_ms DOUBLE,
  input_count INTEGER,
  output_count INTEGER,
  trace_id VARCHAR,
  span_id VARCHAR,
  cache_hit VARCHAR,
  payload_json VARCHAR
)
```

Preserve this append-only raw table. It is useful because `payload_json` captures richer payloads than fixed columns and lets schema evolve without breaking writes.

### MotherDuck Sync

Current file:

- `src/kindly_web_search_mcp_server/analytics/motherduck_sync.py`

Keep:

- `sync-analytics` CLI.
- `analytics_event_raw` mirror table.
- Event-id dedupe with `NOT EXISTS`.
- Views for provider, branch, merged, reranked, final, candidate survival, fetch, answer, rewrite, run timeline, and daily event summary.

Refine:

- Add sync metadata table.
- Add better fixed-column normalization.
- Add more local views, not only MotherDuck views.

## 4. Required Fixes Before Adding More Data

### P0. Normalize Provider Column

Problem:

- `provider.search.result` emits `provider_name`.
- `duckdb_store._event_value(payload, "provider")` does not read `provider_name`.
- Local verification found `provider IS NULL` for all provider-result rows.

Fix:

```python
def _provider_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider")
    if value is None:
        value = payload.get("provider_name")
    if isinstance(value, str):
        return value
    return None
```

Use this in `append_event()` for the fixed `provider` column and in schema backfill:

```sql
UPDATE search_events
SET provider = coalesce(
  provider,
  json_extract_string(payload_json, '$.provider'),
  json_extract_string(payload_json, '$.provider_name')
)
WHERE provider IS NULL;
```

### P0. Normalize Counts

Several events emit `result_count`, `merged_result_count`, `final_result_count`, `input_result_count`, `output_result_count`, and `num_results_requested`, but fixed columns read only `input_count` and `output_count`.

Recommended mappings:

- `input_count`: `input_count`, `input_result_count`, `input_list_count`, `num_results_requested`, `num_results`
- `output_count`: `output_count`, `result_count`, `merged_result_count`, `final_result_count`, `output_result_count`, `total_returned`, `success_count`

### P0. Make `payload_json` JSON-Typed In Views

The base table can remain `VARCHAR` for migration safety, but all views should cast once:

```sql
WITH e AS (
  SELECT *, json(payload_json) AS payload
  FROM analytics_event_raw
)
```

Then views should use `payload` instead of reparsing `payload_json` repeatedly.

## 5. Final Data Model

### 5.1 Raw Table

Keep one raw append-only table:

```sql
analytics_event_raw AS SELECT * FROM search_events
```

This is the source of truth.

### 5.2 Normalized Base View

Create locally and in MotherDuck:

```sql
CREATE OR REPLACE VIEW vw_events AS
SELECT
  event_id,
  recorded_at,
  coalesce(run_key, trace_id, event_id) AS run_key,
  event_name,
  tool_name,
  phase,
  query,
  normalized_query,
  research_goal,
  coalesce(
    provider,
    json_extract_string(payload_json, '$.provider'),
    json_extract_string(payload_json, '$.provider_name')
  ) AS provider,
  model,
  duration_ms,
  input_count,
  output_count,
  trace_id,
  span_id,
  cache_hit,
  json(payload_json) AS payload
FROM analytics_event_raw;
```

### 5.3 Candidate Views

Keep and refine current views:

- `vw_provider_results`
- `vw_branch_candidates`
- `vw_merged_results`
- `vw_rerank_results`
- `vw_search_results`
- `vw_candidate_survival`

Add required columns:

- `url_hash`
- `result_hash`
- `title_len`
- `snippet_len`
- `rank_position`
- `stage_rank`
- `stage_weight`
- `first_seen_stage`
- `last_seen_stage`
- `survived_to_final BOOLEAN`

The app already serializes `link_hash`, `result_hash`, `title_len`, and `snippet_len` for bounded logging; views should extract these fields when present and compute fallback hashes when missing.

### 5.4 Query-Rewrite Views

Add:

- `vw_rewrite_variants`
- `vw_rewrite_models`
- `vw_rewrite_provider_targets`

Required fields:

- `variant_rank`
- `kind`
- `target`
- `rewritten_query`
- `weight`
- `why`
- `policy`
- `intent`
- `models_used`
- `providers_requested`
- `active_provider_names`
- `final_query_count`

Rationale: query rewrite changes provider coverage and branch weights, so analytics must expose variant intent and branch target, not only final result quality.

### 5.5 Cache Views

Add events first, then views:

- `cache.lookup.exact`
- `cache.lookup.semantic`
- `cache.lookup.page`
- `cache.store.exact`
- `cache.store.semantic`
- `cache.store.page`

Views:

- `vw_cache_lookups`
- `vw_cache_stores`
- `vw_semantic_cache_thresholds`
- `vw_cache_ttl_capture`

Required fields:

- `cache_type`: `exact`, `semantic`, `page`
- `lookup_result`: `hit`, `miss`, `expired`, `below_threshold`, `lookup_error`, `decode_error`, `embedding_error`
- `cache_hit`
- `duration_ms`
- `age_seconds`
- `ttl_seconds`
- `providers_key` / `provider_key`
- `similarity_score`
- `vector_distance`
- `search_type`
- `content_type`
- `url_hash`
- `query_hash`
- `word_count`
- `extraction_method`

### 5.6 Content Extraction Views

Current `tool.get_content.response` and `tool.batch_get_content.response` are not enough because they expose only final artifacts.

Add events:

- `content.stage.attempt`
- `content.stage.result`
- `content.stage.fallback`

Required fields:

- `input_url`
- `normalized_url`
- `stage_name`: `stackexchange`, `github_issues`, `github_discussions`, `wikipedia`, `arxiv`, `http_extract`, `browser`
- `backend`
- `status`: `attempted`, `success`, `failed`, `skipped`
- `duration_ms`
- `error_type`
- `error_category`
- `fallback_reason`
- `content_length`
- `word_count`
- `metadata_title`
- `domain`
- `content_type`
- `source_type`
- `summary_mode`
- `focus_query`
- `links_count`
- `selector_strip_count`

Views:

- `vw_fetch_events`
- `vw_content_stage_attempts`
- `vw_content_domain_quality`
- `vw_content_backend_quality`

### 5.7 Provider Health And Circuit Views

Add events from `search/provider_health.py`:

- `provider.health.success`
- `provider.health.failure`
- `provider.health.cooldown_opened`
- `provider.health.cooldown_expired`
- `provider.health.reset`

Required fields:

- `provider`
- `consecutive_failures`
- `cooldown_seconds`
- `cooldown_remaining_s`
- `total_failures`
- `total_successes`
- `error_type`
- `error_message`

Views:

- `vw_provider_health_events`
- `vw_provider_reliability`
- `vw_provider_cooldown_timeline`

### 5.8 Middleware And Session Views

Add events:

- `middleware.rate_limit.acquired`
- `middleware.rate_limit.throttled`
- `middleware.expensive_tool.blocked`
- `middleware.expensive_tool.allowed`
- `session.started`
- `session.activity`
- `session.expired`

Required fields:

- `session_id`
- `client_id`
- `tool_name`
- `request_id`
- `tier`: `cheap`, `expensive`
- `wait_duration_ms`
- `tokens_remaining`
- `attempt_count`
- `steering_reason`
- `session_age_seconds`
- `tool_call_count`

Views:

- `vw_session_activity`
- `vw_tool_steering`
- `vw_rate_limit_waits`

### 5.9 Error Classification Views

Add or enrich all `.error` events with:

- `error_category`: `auth`, `rate_limit`, `network`, `timeout`, `content`, `config`, `schema`, `provider`, `unknown`
- `provider`
- `tool_name`
- `recoverable BOOLEAN`
- `user_guidance`
- `retry_after_seconds`

Views:

- `vw_error_events`
- `vw_error_rate_by_category`
- `vw_provider_error_taxonomy`

## 6. MCP Eval Data Model

The existing `mcp_advanced_research_insights.md` correctly says MCP evals should use real trajectories, not only final answers. The implementation should not add a hidden `deep_research` tool. Instead, add eval tables and join them to normal event data.

### Eval Tables

```sql
CREATE TABLE IF NOT EXISTS eval_runs (
  eval_run_id VARCHAR,
  recorded_at TIMESTAMP,
  suite_name VARCHAR,
  suite_version VARCHAR,
  evaluator VARCHAR,
  git_commit VARCHAR,
  duckdb_schema_version INTEGER,
  notes VARCHAR
);

CREATE TABLE IF NOT EXISTS eval_cases (
  eval_case_id VARCHAR,
  eval_run_id VARCHAR,
  case_name VARCHAR,
  user_goal VARCHAR,
  expected_tool_sequence_json VARCHAR,
  expected_domains_json VARCHAR,
  expected_answer_traits_json VARCHAR,
  difficulty VARCHAR
);

CREATE TABLE IF NOT EXISTS eval_observations (
  eval_observation_id VARCHAR,
  eval_run_id VARCHAR,
  eval_case_id VARCHAR,
  run_key VARCHAR,
  trace_id VARCHAR,
  observed_tool_sequence_json VARCHAR,
  final_answer VARCHAR,
  deterministic_score DOUBLE,
  judge_score DOUBLE,
  pass BOOLEAN,
  failure_category VARCHAR,
  judge_rationale VARCHAR,
  payload_json VARCHAR
);
```

### Eval Views

- `vw_eval_case_timeline`: joins `eval_observations.run_key` to `vw_run_timeline`
- `vw_eval_candidate_survival`: joins eval cases to `vw_candidate_survival`
- `vw_eval_provider_quality`: provider yield, survival, and judged quality by eval suite
- `vw_eval_fetch_quality`: fetch backend success and content quality by eval suite

### Eval Metrics

Track:

- Tool discovery correctness.
- Argument schema adherence.
- Search result relevance.
- Candidate survival from provider to final response.
- Fetch success for selected URLs.
- Citation/source grounding for `gemini_search`, `perplexity_search`, and `quick_web_search`.
- Multi-turn recovery when a provider errors or a fetch fails.
- Latency and cost proxies per case.

## 7. Analytics Query Surfaces

### Local CLI Query Module

Add `analytics/queries.py` with parameterized SQL functions. These should work on local DuckDB and MotherDuck by changing only the table/view prefix.

Required functions:

- `provider_performance(days: int = 7)`
- `cache_hit_rates(days: int = 7)`
- `semantic_threshold_curve(days: int = 30)`
- `candidate_survival(days: int = 7)`
- `content_backend_quality(days: int = 30)`
- `domain_fetch_quality(days: int = 30, min_fetches: int = 5)`
- `rewrite_variant_quality(days: int = 7)`
- `error_taxonomy(days: int = 7)`
- `session_tool_usage(days: int = 7)`
- `eval_quality_summary(eval_run_id: str | None = None)`

### NL2SQL And MCP Tool

NL2SQL is part of the first analytics interface. The design is not "defer NL2SQL"; it is "ship NL2SQL with hard execution constraints."

Expose two complementary interfaces:

- `analytics_report(report_name: Literal[...], days: int = 7)`.
- `analytics_query(question: str, scope: Literal["local", "motherduck"] = "local", max_rows: int = 100)`.

The deterministic report tool gives stable canned summaries. The NL2SQL tool handles exploratory analysis over the same view layer.

Required NL2SQL guardrails:

- read-only DuckDB connection,
- fixed allowlist of views,
- query timeout,
- required `LIMIT`,
- no DDL/DML,
- generated SQL displayed in the response,
- execution error returned with one bounded self-correction attempt,
- result row and cell-size caps,
- no secret/env-var/schema-hidden data in prompt context.

For local DuckDB, this can query `.kindly/analytics/search_events.duckdb` plus local views and optional local Flock-derived tables. For MotherDuck, this queries only the synced standard SQL tables/views.

### Local Flock / LLM-in-SQL Lane

Flock/FlockMTL is an active local-DuckDB analytics track, not a MotherDuck production dependency.

Use it locally for:

- semantic quality scoring of search candidates,
- summarizing error clusters,
- query-intent labeling,
- answer/citation quality classification,
- eval-case judging where deterministic checks are insufficient.

Do not put Flock-dependent SQL in MotherDuck views or Grafana panels. Instead, local Flock jobs should materialize their outputs into ordinary DuckDB tables that can be synced to MotherDuck:

```sql
CREATE TABLE IF NOT EXISTS llm_quality_scores (
  score_id VARCHAR,
  recorded_at TIMESTAMP,
  source_event_id VARCHAR,
  run_key VARCHAR,
  eval_run_id VARCHAR,
  scoring_model VARCHAR,
  scoring_prompt_version VARCHAR,
  target_type VARCHAR,
  target_id VARCHAR,
  score DOUBLE,
  label VARCHAR,
  rationale VARCHAR,
  payload_json VARCHAR
);
```

MotherDuck then consumes `llm_quality_scores` as a normal table, without requiring Flock in the cloud.

## 8. MotherDuck Design

### Schema

Default schema:

- `kindly_analytics`

Tables:

- `analytics_event_raw`
- `analytics_sync_state`
- `analytics_event_daily`
- `eval_runs`
- `eval_cases`
- `eval_observations`

Views:

- All `vw_*` views listed above.

### Sync

Keep event-id dedupe:

```sql
INSERT INTO target.analytics_event_raw BY NAME
SELECT local.*
FROM search_events AS local
WHERE NOT EXISTS (
  SELECT 1
  FROM target.analytics_event_raw AS remote
  WHERE remote.event_id = local.event_id
)
ORDER BY local.recorded_at;
```

Add `analytics_sync_state`:

```sql
CREATE TABLE IF NOT EXISTS analytics_sync_state (
  sync_id VARCHAR,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  source_path VARCHAR,
  source_rows INTEGER,
  inserted_rows INTEGER,
  duckdb_version VARCHAR,
  motherduck_database VARCHAR,
  schema_name VARCHAR,
  status VARCHAR,
  error_message VARCHAR
);
```

### Version Guard

Before loading the MotherDuck extension or syncing:

1. Query local `duckdb.__version__`.
2. Compare against the documented MotherDuck-supported range for the configured region.
3. If outside range, fail sync with a clear error. Do not silently proceed.

For this repo as of this plan, prefer `duckdb>=1.4.1,<1.4.5` for MotherDuck sync extras, while keeping runtime tests aware of the existing `pyproject.toml` pin until implementation changes it.

## 9. Grafana Design

Keep existing MotherDuck-backed panels:

- Candidate survival by stage.
- Provider yield over time.
- SearXNG engine quality.

Add panels:

- Cache hit rate by cache type.
- Semantic cache similarity bucket vs hit/miss.
- Provider cooldown timeline.
- Provider p95/p99 latency with error-rate overlay.
- Content backend success and average word count.
- Domain fetch quality.
- Rewrite policy and variant target mix.
- Final-answer citation count and source count.
- Eval pass rate by suite and failure category.

## 10. Implementation Sequence

### Phase 1: Normalize And Backfill

Files:

- `analytics/duckdb_store.py`
- `analytics/motherduck_sync.py`
- `tests/test_duckdb_analytics.py`

Tasks:

- Normalize provider aliases.
- Normalize count aliases.
- Backfill provider and count columns in `_ensure_schema`.
- Add `vw_events`.
- Add tests proving `provider.search.result` populates fixed `provider`.

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_duckdb_analytics.py -q
.\.venv\Scripts\python.exe -m ruff check src/kindly_web_search_mcp_server/analytics tests/test_duckdb_analytics.py
```

### Phase 2: Add Local Views

Files:

- `analytics/views.py` or `analytics/sql.py`
- `analytics/motherduck_sync.py`
- `analytics/queries.py`

Tasks:

- Move view SQL out of `motherduck_sync.py`.
- Install views locally and remotely.
- Add deterministic query functions.

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_duckdb_analytics.py -q
```

### Phase 3: Add Missing Event Emitters

Files:

- `cache/query_cache.py`
- `cache/semantic_cache.py`
- `cache/page_cache.py`
- `search/provider_health.py`
- `middleware/rate_limits.py`
- `middleware/expensive_tool_protection.py`
- `middleware/session_tracking.py`
- `content/resolver.py`
- `server.py`

Tasks:

- Add cache lookup/store events.
- Add provider health events.
- Add middleware/session events.
- Add content stage attempt/fallback events.
- Enrich error events with `classify_error()`.

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_duckdb_analytics.py tests/test_observability_flow.py tests/test_observability_logging.py -q
.\.venv\Scripts\python.exe -m ruff check src/kindly_web_search_mcp_server tests
```

### Phase 4: Add Eval Tables And Reports

Files:

- `analytics/evals.py`
- `analytics/queries.py`
- tests under `tests/test_analytics_evals.py`

Tasks:

- Create eval tables locally.
- Sync eval tables to MotherDuck.
- Join eval observations to event timeline and candidate survival.
- Add deterministic report APIs.

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_analytics_evals.py tests/test_duckdb_analytics.py -q
```

### Phase 5: MotherDuck Sync Validation

Prerequisites:

- `MOTHERDUCK_TOKEN`
- `KINDLY_MOTHERDUCK_DATABASE`
- DuckDB version compatible with MotherDuck region.

Command:

```powershell
.\.venv\Scripts\kindly-web-search.exe sync-analytics --limit 100
```

Validate:

- rows inserted,
- `vw_events` query works,
- `vw_candidate_survival` query works,
- Grafana panels query MotherDuck datasource successfully.

## 11. Explicit Rejections From Earlier Plans

Reject as production defaults:

- Hidden `deep_research` agent tool that internally chains search/fetch without caller control.
- Flock/FlockMTL inside MotherDuck dashboards or remote MotherDuck view SQL.
- Community DuckDB extensions inside MotherDuck dashboards.
- Replacing LanceDB caches with DuckDB tables. LanceDB remains cache/vector storage; DuckDB stores analytics events about cache behavior.

Accept as active design lanes:

- Local Flock or other local LLM-in-SQL extension outputs materialized to ordinary tables.
- Read-only NL2SQL analytics assistant with strict view allowlist and SQL/result caps.
- Python ML anomaly detection over DuckDB-extracted features.
- Query clustering with embeddings.

## 12. Definition Of Done

The DuckDB/MotherDuck analytics design is implemented when:

- `provider` fixed column is populated for provider events.
- Local and MotherDuck `vw_events` and candidate-survival views work.
- Cache, middleware/session, provider-health, content-stage, and classified-error events are persisted.
- Deterministic analytics reports work locally without MotherDuck.
- MotherDuck sync writes raw tables, eval tables, views, summaries, and sync-state rows.
- Grafana uses MotherDuck views for candidate survival, provider health, cache quality, content quality, and eval quality.
- Tests cover schema migration, event normalization, view SQL generation, and eval joins.
- Docs and changelog describe the final analytics contract.

## 13. Sources

- DuckDB Python persistent file and connection guidance: https://duckdb.org/docs/current/clients/python/overview
- DuckDB JSON extraction and JSON table functions: https://duckdb.org/docs/current/data/json/json_functions
- MotherDuck architecture, `ATTACH 'md:'`, dual execution, regional DuckDB version support, and extension limitations: https://motherduck.com/docs/concepts/architecture-and-capabilities/
- MotherDuck loading from local files/databases: https://motherduck.com/docs/key-tasks/loading-data-into-motherduck/loading-data-from-local-machine/
- MotherDuck plugin docs answers in this session for local-to-MotherDuck sync, JSON/views, Grafana, and compatibility guidance.
