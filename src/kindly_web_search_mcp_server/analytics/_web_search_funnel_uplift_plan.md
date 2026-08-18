# Web Search Funnel Analytics Uplift — Refined Plan

**Status:** proposal (planning only — no implementation)
**Direction:** follows the prototype schema model (`_prototype_schema_model.py`) with corrections grounded in the actual runtime

## 1. Prototype Evaluation

### 1.1 What the prototype gets right

The prototype proposes a fundamentally better analytics model than the current `search_runs`/`search_branches`/`provider_calls`/`search_candidates`/`rerank_*`/`final_results` funnel. Its core innovations are:

| Innovation | Why it matters | Current gap it fixes |
|---|---|---|
| **`result_catalog`** — a cross-run canonical URL registry | Enables "was this URL seen before across runs?" and deduplicates results across time | Current `search_candidates` is per-run; no cross-run identity |
| **`provider_results`** — per-provider-per-candidate grain | Shows which provider found which candidate at which rank — the basic provenance question | Current `provider_calls.candidate_urls` is a flat list; no candidate-level provenance |
| **`query_variants`** — explicit variant lifecycle (proposed/selected/executed/skipped) | Makes the rewrite planner's decision process inspectable | Current `search_branches` only records executed branches; proposed-but-skipped variants are lost |
| **`candidate_stage_events`** — per-candidate-per-rerank-stage survival | Shows exactly which candidates entered, survived, or were removed at each stage — the funnel question | Current `rerank_candidates` records rank_before/after but no `entered`/`survived`/`removal_reason` semantics |
| **`tool_events` / `tool_output_items`** — cross-tool correlation | Links web_search output to get_content fetches via `canonical_result_id` + session attribution | Current `tool_calls` has no `run_key` and no output-item linkage |
| **Stable IDs** (`branch_id`, `provider_call_id`, `canonical_result_id`) | Durable join keys that survive re-runs and are deterministic | Current ordinal-based keys (`branch_index`, `response_index`) are fragile and non-portable |

### 1.2 What the prototype gets wrong or oversimplifies

| Issue | Detail | Correction |
|---|---|---|
| **`session_id NOT NULL` on `search_runs`** | The runtime makes `session_id` optional (it's often absent per the live audit). | Make `session_id` nullable on all tables. |
| **`provider_calls.status` CHECK constraint** | Constrains to `('success', 'empty', 'error')` but the runtime also produces `'incomplete'` and `'skipped'`. | Use the full runtime status set or drop the CHECK. |
| **`rerank_stage_executions.status` CHECK** | Constrains to `('success', 'skipped', 'error')` but the runtime also produces `'fallback_success'` and `'failed_open'`. | Add all runtime statuses. |
| **`content_fetches.canonical_result_id NOT NULL`** | Not every content fetch has a known `canonical_result_id` (e.g., user-supplied URLs not from search). | Make nullable. |
| **`final_results.canonical_result_id NOT NULL`** | The current runtime hardcodes this to `None` in `outcomes.py:210`. The prototype assumes it's always populated. | Either make nullable or fix the runtime to populate it (preferred — see §3.2). |
| **`query_variants` has `parent_variant_id`** | The planner doesn't produce a variant DAG — it produces 5 flat rewrites plus the original. There's no parent-child relationship. | Drop `parent_variant_id`; keep `variant_order` and `variant_role`. |
| **`embedding FLOAT[3]`** | The prototype uses 3-dimensional embeddings for testability. Production uses 1024-dim. | Use `FLOAT[1024]` or a configurable dimension. |
| **`candidate_stage_events.entered`/`survived`** | The current rerank path doesn't emit "entered" — it only records candidates that existed before and after. "Entered" (new candidates added mid-stage) is not a current runtime concept. | Derive `entered = rank_before IS NULL AND rank_after IS NOT NULL` from existing data. |
| **`tool_output_items.item_type`** | The runtime doesn't emit typed output items — `web_search` returns `WebSearchResponse.results` and other tools return their own shapes. | Either define item types per tool or keep generic with `tool_name` discriminator. |
| **No `payload_json`** | The prototype has no JSON payload retention on most tables. The current schema uses `payload_json` extensively for redaction-safe retention of shapes that aren't stable enough to flatten. | Keep `payload_json JSON` on all fact tables for forward compatibility. |
| **`vw_followup_attribution` inferred edges** | The 30-minute same-session inference is creative but has no runtime evidence that this attribution is meaningful. | Keep as an explicit analytical hypothesis view, but label it "inferred" clearly and don't treat it as ground truth. |

### 1.3 Prototype vs current runtime: grain compatibility

| Prototype grain | Current grain | Migration path |
|---|---|---|
| `search_runs` PK `run_key` | Same — `run_key` is already the PK | Additive: add stable ID columns, keep `run_key` |
| `query_variants` — one row per planned variant | No current equivalent — `search_branches` only records executed branches | **New table**: additive, doesn't replace branches |
| `search_branches` PK `branch_id` | Current PK is `(run_key, branch_index)` | Additive: generate `branch_id = hash(run_key, branch_index)` and add as column |
| `provider_calls` PK `provider_call_id` | Current PK is `(run_key, branch_index, provider)` | Additive: generate `provider_call_id = hash(run_key, branch_index, provider)` |
| `result_catalog` — cross-run canonical URL registry | No current equivalent | **New table**: additive |
| `provider_results` — per-provider-per-candidate | No current equivalent — `candidate_urls` is a flat list | **New table**: additive, instrumentation-required |
| `search_candidates` PK `(run_key, canonical_result_id)` | Current PK is `(run_key, link)` | Additive: add `canonical_result_id` column, derive from link via hash |
| `candidate_stage_events` — per-candidate-per-stage | Current `rerank_candidates` is close but lacks `entered`/`survived` semantics | Additive: add columns to `rerank_candidates` or create new bridge |
| `final_results` — add `canonical_result_id` | Current has the column but it's `None` | **Fix runtime**: wire `observability_ids._canonical_result_id()` into `outcomes.py` |
| `tool_events` | Current `tool_calls` is close but lacks `run_key` | Additive: add `run_key` column to `tool_calls` |
| `tool_output_items` | No current equivalent | **New table**: additive, instrumentation-required |

## 2. Design principles for the refined plan

Following the MotherDuck modeling playbook:

1. **Wide denormalized fact tables** — prefer wide tables over repeated joins for common analytical access paths.
2. **Explicit grains with comments** — every table declares its grain in a comment.
3. **Stable IDs as VARCHAR** — deterministic hashes for cross-run identity.
4. **NOT NULL where the runtime guarantees a value** — nullable everywhere the runtime doesn't.
5. **Additive migrations** — `ALTER TABLE ADD COLUMN` for existing tables, `CREATE TABLE IF NOT EXISTS` for new tables.
6. **`payload_json` on every fact table** — redaction-safe retention of unstable shapes.
7. **Views for reusable logic, CTAS for materialized results** — views for always-current analytics, CTAS for expensive rollups.
8. **Raw / staging / analytics lifecycle stages** — separate the layers when the project is non-trivial.

## 3. Refined uplift plan

### Phase 0: Fix existing persistence gaps (no schema changes)

These are bugs in the current writer path, not schema design issues. They must be fixed before any schema uplift is meaningful.

#### 3.1 Wire `canonical_result_id` into `final_results`

**Current:** `outcomes.py:209-210` hardcodes `"candidate_id": None, "canonical_result_id": None`.

**Fix:** Import `_candidate_id` and `_canonical_result_id` from `analytics/observability_store.py` (already used by `rerank/observability.py:172-173`) and populate them:

```python
"candidate_id": _candidate_id(res.link, res.title, res.snippet),
"canonical_result_id": _canonical_result_id(res.link),
```

**Impact:** Unblocks the `final_results` → `rerank_candidates` join on `canonical_result_id`, which is the core funnel-join the prototype enables.

**Verify:** Write a test that asserts `final_results.canonical_result_id IS NOT NULL` for successful runs.

#### 3.2 Wire `retry_after`/`retryable` into `provider_calls`

**Current:** `search/retrieval.py` records `retry_after`/`retryable` in the `common` dict, but `outcomes.py` drops them.

**Fix:** Add `retry_after_seconds DOUBLE` and `retryable BOOLEAN` columns to `provider_calls` via `_ensure_columns`, and pass them through from the retrieval layer.

**Verify:** Assert `provider_calls.retry_after_seconds IS NOT NULL` when a 429 is returned.

#### 3.3 Remove or populate always-NULL `rerank_stages` columns

**Current:** `score_threshold`, `alpha_blend`, `instruction_present`, `instruction_length`, `query_type_hint`, `entity_overlap_enabled` are always `NULL` because `RerankStageSummary` doesn't set them.

**Fix:** Either:
- (a) Wire the rerank summary to populate them from the actual rerank stage data (the values exist in `emit_rerank_summary` parameters), or
- (b) Drop them if they're not useful.

**Verify:** Assert columns are populated when the corresponding rerank stage runs.

### Phase 1: Additive stable IDs on existing tables (schema-only, no runtime break)

Add stable ID columns to existing tables. These are derived from existing data, so they can be backfilled.

#### 3.4 `search_branches.branch_id`

```sql
ALTER TABLE search_branches ADD COLUMN branch_id VARCHAR;
COMMENT ON COLUMN search_branches.branch_id IS 'Stable hash of (run_key, branch_index) — durable cross-run identity';
```

Backfill: `UPDATE search_branches SET branch_id = md5(run_key || '|' || CAST(branch_index AS VARCHAR))`

#### 3.5 `provider_calls.provider_call_id`

```sql
ALTER TABLE provider_calls ADD COLUMN provider_call_id VARCHAR;
COMMENT ON COLUMN provider_calls.provider_call_id IS 'Stable hash of (run_key, branch_index, provider) — durable cross-run identity';
```

Backfill: `UPDATE provider_calls SET provider_call_id = md5(run_key || '|' || COALESCE(CAST(branch_index AS VARCHAR),'') || '|' || provider)`

#### 3.6 `search_candidates.canonical_result_id`

```sql
ALTER TABLE search_candidates ADD COLUMN canonical_result_id VARCHAR;
COMMENT ON COLUMN search_candidates.canonical_result_id IS 'Stable hash of canonicalized link — cross-run result identity';
```

Backfill: `UPDATE search_candidates SET canonical_result_id = md5(lower(trim(link)))`

#### 3.7 `tool_calls.run_key`

```sql
ALTER TABLE tool_calls ADD COLUMN run_key VARCHAR;
COMMENT ON COLUMN tool_calls.run_key IS 'Join key to search_runs when the tool is web_search';
```

This is populated by the observability layer for `web_search` tool calls. For other tools, it remains `NULL`.

### Phase 2: New additive tables (following prototype direction)

These are **new tables** that add analytical capability without replacing existing tables.

#### 3.8 `result_catalog` — cross-run canonical URL registry

**Grain:** one canonical URL, deduplicated across all runs.

```sql
CREATE TABLE IF NOT EXISTS result_catalog (
    canonical_result_id   VARCHAR NOT NULL PRIMARY KEY,
    canonical_url          VARCHAR NOT NULL UNIQUE,
    domain                 VARCHAR NOT NULL,
    title_first_seen       VARCHAR,
    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_seen_run_key     VARCHAR,
    total_run_appearances  INTEGER DEFAULT 0
);
COMMENT ON TABLE result_catalog IS 'Cross-run canonical URL registry — one row per unique URL, deduplicated across all searches';
```

**Writer boundary:** populated when `search_candidates` or `final_results` are written — check if `canonical_result_id` exists, insert if not.

#### 3.9 `provider_results` — per-provider-per-candidate provenance

**Grain:** one candidate result as returned by one provider in one branch.

```sql
CREATE TABLE IF NOT EXISTS provider_results (
    provider_result_id    VARCHAR NOT NULL PRIMARY KEY,
    provider_call_id      VARCHAR NOT NULL,
    run_key               VARCHAR NOT NULL,
    branch_id             VARCHAR NOT NULL,
    provider              VARCHAR NOT NULL,
    provider_rank         INTEGER NOT NULL,
    canonical_result_id   VARCHAR NOT NULL,
    raw_url               VARCHAR NOT NULL,
    title                 VARCHAR,
    snippet               VARCHAR,
    raw_score             DOUBLE,
    is_eligible           BOOLEAN,
    rejection_reason      VARCHAR,
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload_json          JSON
);
COMMENT ON TABLE provider_results IS 'Per-provider-per-candidate provenance — which provider found which result at which rank';
```

**Writer boundary:** `search/retrieval.py::_record_provider_result` — the data is already in `ProviderRankedResults` and `candidate_urls`, but needs to be exploded to per-candidate rows.

**This is the highest-value new table.** It answers "which providers contribute which candidates" — the core provenance question.

#### 3.10 `query_variants` — planner variant lifecycle

**Grain:** one planned query variant (original, rewrite, or expansion).

```sql
CREATE TABLE IF NOT EXISTS query_variants (
    variant_id      VARCHAR NOT NULL PRIMARY KEY,
    run_key         VARCHAR NOT NULL,
    variant_order   INTEGER NOT NULL,
    variant_role    VARCHAR NOT NULL,
    query_text      VARCHAR NOT NULL,
    selected        BOOLEAN NOT NULL DEFAULT FALSE,
    executed        BOOLEAN NOT NULL DEFAULT FALSE,
    skip_reason     VARCHAR,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_key, variant_order)
);
COMMENT ON TABLE query_variants IS 'Planner query variant lifecycle — proposed, selected, executed, or skipped with reason';
```

**Writer boundary:** `search/planning.py::plan_search` — the 5 rewrite queries, seed queries, and original query are all known at plan time. Whether each was selected/executed is known after retrieval.

#### 3.11 `candidate_stage_events` — rerank survival tracking

**Grain:** one candidate's experience in one rerank stage execution.

```sql
CREATE TABLE IF NOT EXISTS candidate_stage_events (
    stage_execution_id   VARCHAR NOT NULL,
    run_key              VARCHAR NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    entered              BOOLEAN NOT NULL,
    survived             BOOLEAN NOT NULL,
    rank_before          INTEGER,
    rank_after           INTEGER,
    score_before         DOUBLE,
    score_after          DOUBLE,
    score_name           VARCHAR,
    removal_reason       VARCHAR,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stage_execution_id, canonical_result_id),
    payload_json         JSON
);
COMMENT ON TABLE candidate_stage_events IS 'Per-candidate per-rerank-stage survival — entered, survived, removed, with scores and removal reason';
```

**Writer boundary:** `rerank/observability.py::record_rerank_candidate_rows` — the data is already available in `build_rerank_candidate_rows` (`rank_before`, `rank_after`, `score_before`, `score_after`). Derive `entered = rank_before IS NULL AND rank_after IS NOT NULL`, `survived = rank_after IS NOT NULL`.

#### 3.12 `tool_output_items` — cross-tool output linkage

**Grain:** one output item from one tool invocation.

```sql
CREATE TABLE IF NOT EXISTS tool_output_items (
    output_item_id       VARCHAR NOT NULL PRIMARY KEY,
    tool_call_id         VARCHAR NOT NULL,
    session_id           VARCHAR,
    run_key              VARCHAR,
    tool_name            VARCHAR NOT NULL,
    item_type            VARCHAR NOT NULL,
    item_rank            INTEGER NOT NULL,
    canonical_result_id VARCHAR,
    raw_url              VARCHAR,
    title                VARCHAR,
    snippet              VARCHAR,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tool_call_id, item_type, item_rank)
);
COMMENT ON TABLE tool_output_items IS 'Output items from any tool invocation — links search results to content fetches via canonical_result_id';
```

**Writer boundary:** the tool response event in `observability.py` — for `web_search`, iterate `response.results`; for other tools, map their output items.

**This is the second-highest-value new table.** It enables the `vw_followup_attribution` view (which search results were later fetched via `get_content`).

### Phase 3: Analytical views

Following the prototype's view design with corrections for the actual runtime model.

#### 3.13 `vw_run_stage_funnel` — the core funnel view

Synthetic stages (provider_raw → provider_unique → eligible → merge → rerank stages → final) combined with actual rerank stage executions. This is the single most important analytical view — it shows where candidates disappear.

#### 3.14 `vw_candidate_trajectory` — per-candidate journey

Tracks a candidate from discovery (which providers/branches found it) through rerank stages (entered, survived, removed) to final ranking. Requires `provider_results` + `candidate_stage_events` + `final_results` joined on `canonical_result_id`.

#### 3.15 `vw_provider_contribution` — provider discovery credit

Fractional discovery credit (1/multiplicity) per provider — how many unique candidates each provider found, how many were exclusive, how many survived to merge/final. Requires `provider_results`.

#### 3.16 `vw_branch_contribution` — branch discovery credit

Same as provider contribution but per branch. Requires `provider_results` with `branch_id`.

#### 3.17 `vw_rewrite_value` — rewrite ROI

For each query variant (original vs rewrite), how many branches were created, how many unique candidates discovered, how many survived to final. Requires `query_variants` + `search_branches`.

#### 3.18 `vw_followup_attribution` — search→content linkage

Links `tool_output_items` (search results) to `content_fetches` (get_content calls) via `canonical_result_id` and session. Explicit edges first (source_output_item_id is set), inferred edges second (same session, same canonical_result_id, within 30 minutes).

#### 3.19 `vw_result_usefulness` — judgment + fetch rollup

Per output item: fetch attempts, successful fetches, max content chars, attribution method, relevance/factuality scores. Requires `judgment_facets` (or current `llm_judgments`) + `content_fetches`.

#### 3.20 `vw_dense_score_calibration` — rerank score vs survival

Score bin → survival rate → final rate → avg relevance. Requires `candidate_stage_events` + `final_results` + `llm_judgments`.

### Phase 4: Materialized summary tables

Following the MotherDuck playbook pattern for expensive rollups.

#### 3.21 `summary_provider_discovery_daily`

```sql
CREATE OR REPLACE TABLE summary_provider_discovery_daily AS
SELECT
    date_trunc('day', recorded_at)::DATE AS day,
    provider,
    COUNT(*) AS total_calls,
    COUNT(DISTINCT run_key) AS distinct_runs,
    SUM(hit_count) AS total_hits,
    AVG(hit_count) AS avg_hits_per_call,
    COUNT(*) FILTER (WHERE status = 'error') AS error_count
FROM provider_calls
GROUP BY ALL;
```

#### 3.22 `summary_rewrite_value_daily`

Similar rollup for `query_variants` — per day, per variant_role, how many were proposed/selected/executed and how many candidates they contributed.

## 4. Rollout order

1. **Phase 0** (bug fixes): Wire `canonical_result_id` into `final_results`; wire `retry_after` into `provider_calls`; fix or drop always-NULL `rerank_stages` columns. — *Verify: existing tests pass + new assertions on non-NULL values.*
2. **Phase 1** (additive IDs): Add `branch_id`, `provider_call_id`, `canonical_result_id`, `run_key` columns to existing tables. Backfill via UPDATE. — *Verify: joins on new IDs return expected counts.*
3. **Phase 2** (new tables): Create `result_catalog`, `provider_results`, `query_variants`, `candidate_stage_events`, `tool_output_items`. — *Verify: each table populates from its declared writer boundary.*
4. **Phase 3** (views): Create the 8 analytical views. — *Verify: each view returns rows against a populated test database.*
5. **Phase 4** (summaries): Create materialized summary tables and wire refresh commands. — *Verify: refresh produces non-zero rows.*

## 5. Explicit non-goals

- Do not replace the existing `search_runs`/`search_branches`/`provider_calls`/`search_candidates`/`rerank_stages`/`rerank_candidates`/`final_results` tables.
- Do not drop the current ordinal-based composite keys — the new stable IDs are additive.
- Do not create a second web-search writer — extend the existing `outcomes.py::persist_search_outcome` path.
- Do not add HNSW indexes until vector-count/latency benchmarks justify them (per prototype's `VSS_RECOMMENDATION`).
- Do not infer `canonical_result_id` from URLs that haven't been canonicalized by `url_canonicalize.py`.
- Do not treat inferred follow-up attribution edges as ground truth.

## 6. Data type guidance (per MotherDuck modeling playbook)

| Field type | Recommended DuckDB type | Notes |
|---|---|---|
| Stable IDs | `VARCHAR` | Handles UUIDs and hash strings |
| Timestamps | `TIMESTAMPTZ` | Preserves timezone |
| Scores | `DOUBLE` | Search scores are not monetary |
| Counts | `INTEGER` | Sufficient for result/candidate counts |
| Booleans | `BOOLEAN` | Clear intent |
| Lists (providers, tags) | `VARCHAR[]` | Supports unnesting |
| Semi-structured payloads | `JSON` | Redaction-safe retention |
| URLs | `VARCHAR` | No special type needed |

## 7. Constraint guidance

Per the MotherDuck playbook, DuckDB only enforces `NOT NULL`. `PRIMARY KEY`, `UNIQUE`, and `CHECK` are informational only — they document intent but don't prevent bad writes. Use:

- `NOT NULL` aggressively on fields the runtime guarantees.
- `PRIMARY KEY` as documentation of intended uniqueness.
- `UNIQUE` as documentation of intended deduplication.
- `CHECK` only when the constraint is genuinely useful for documentation (e.g., `survived = FALSE OR entered = TRUE` in `candidate_stage_events`).
- Rely on idempotent writer behavior (`ON CONFLICT DO NOTHING`) for actual deduplication.