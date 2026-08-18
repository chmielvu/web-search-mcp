# Full Search Analytics Expansion — DuckDB Design

**Status:** revised proposal

## 1. Scope and design decision

Extend the existing DuckDB analytics sink so the application can explain the full search workflow without duplicating the authoritative `web_search` funnel or inventing runtime metadata.

Covered tools:

- `web_search` — the existing multi-provider search, rewrite, candidate, rerank, and quality funnel;
- `quick_web_search` — Parallel AI reconnaissance search;
- `gemini_search` — Gemini + Google Search grounded synthesis;
- `code_search` — multi-channel public-code retrieval, ranking, hydration, and cloud reranking;
- `get_content` and `batch_get_content` — content retrieval and LLM summarization;
- `discover_links`, `generate_sitemap`, `academic_search`, and `grok_search` — visible through generic cross-tool coverage now, with typed facts deferred until their contracts and observed terminal events justify separate grains.

This is a **DuckDB** design. The MotherDuck modeling skill is used only for transferable OLAP guidance: explicit grains, explicit types, LIST/JSON where appropriate, additive migrations, and reusable views. No MotherDuck database, namespace, sync contract, or deployment behavior is assumed.

### Decision

1. Keep the existing `web_search` tables as the authoritative web-search funnel.
2. Do not create a second copy of `search_runs`, `search_branches`, `provider_calls`, `search_candidates`, `rerank_stages`, or `final_results`.
3. Add typed facts for quick search, Gemini search, code search, content operations, and final summary outputs.
4. Keep `tool_calls` as the cross-tool event baseline and add coverage/linkage views over it.
5. Keep attempt facts separate from final output facts.
6. Treat unavailable values as `NULL`; never reconstruct them from public projections, aggregate counts, or guessed joins.

Every proposed field is classified as one of:

- **Runtime-produced:** present in an authoritative runtime object or tool-boundary event;
- **Existing-DuckDB-produced:** already emitted by the existing web-search outcome path;
- **Instrumentation-required:** a valid planned field that needs a deliberate runtime change;
- **Derived:** computed from rows at the declared grain;
- **Unavailable:** intentionally `NULL` until a source exists.

The current analytics implementation is not treated as the authority for runtime behavior. Runtime modules and the read-only database audit are the evidence sources for this proposal.

## 2. Read-only live DuckDB audit

Source: `duckdb_data/analytics/search_events.duckdb`, opened with DuckDB `-readonly`. These values describe the observed database snapshot; they are not target volumes or complete production history.

### 2.1 Generic `tool_calls` coverage

`tool_calls` is the only current event table that captures all of the following tool families.

| Tool | Event rows | Distinct parent IDs | Terminal events observed | Range |
|---|---:|---:|---|---|
| `web_search` | 579 | 360 | 225 success, 3 error | 2026-07-25 → 2026-08-18 |
| `quick_web_search` | 182 | 91 | 91 success | 2026-07-26 → 2026-08-18 |
| `gemini_search` | 68 | 34 | 25 success, 9 error | 2026-07-26 → 2026-08-16 |
| `code_search` | 266 | 136 | 111 success, 14 empty, 5 error | 2026-08-16 → 2026-08-18 |
| `get_content` | 1,436 | 544 | 528 success, 93 error, 36 partial, 25 blocked | 2026-07-25 → 2026-08-18 |
| `batch_get_content` | 295 | 190 | 98 success, 5 empty, 3 error | 2026-07-26 → 2026-08-18 |
| `discover_links` | 47 | 35 | 11 success, 1 error | 2026-07-27 → 2026-08-18 |
| `academic_search` | 3 | 3 | no response event observed | 2026-08-18 only |
| `generate_sitemap` | 1 | 1 | no response event observed | 2026-08-08 only |

`tool_call_id` is present on every observed event row, but one ID spans request and terminal phases. It is a parent invocation key, not an event-row key.

Correlation is incomplete:

- `session_id` is effectively absent except for a small subset of quick-search request events;
- `trace_id` is partial and tool-dependent;
- `search_runs.tool_call_id` is populated for only 89 of 589 existing web-search runs;
- only 78 distinct web-search IDs currently join from `tool_calls` to `search_runs`;
- `search_branches` and `provider_calls` are keyed by `run_key`, not `tool_call_id`;
- unmatched generic events must remain unmatched.

When facts are projected from `tool_calls`, retain the source terminal `event_id` as `terminal_event_id`. A parent `tool_call_id` alone is not a safe primary key when a parent can have multiple terminal/error events.

### 2.2 Existing typed web-search funnel

| Table | Rows | Distinct run keys | Range |
|---|---:|---:|---|
| `search_runs` | 589 | 589 | 2026-07-16 → 2026-08-18 |
| `search_branches` | 3,494 | 583 | 2026-07-16 → 2026-08-18 |
| `provider_calls` | 7,287 | 582 | 2026-07-16 → 2026-08-18 |
| `search_candidates` | 37,580 | 575 | 2026-07-16 → 2026-08-18 |
| `rerank_candidates` | 43,022 | 555 | 2026-07-21 → 2026-08-18 |
| `rerank_stages` | 1,662 | 555 | 2026-07-16 → 2026-08-18 |
| `final_results` | 8,380 | 559 | 2026-07-16 → 2026-08-18 |
| `search_quality_scores` | 402 | 402 | 2026-07-16 → 2026-08-18 |
| `llm_judgments` | 2,647 | 312 judged runs | 2026-07-20 → 2026-08-18 |
| `judge_evaluations` | 18 | 18 | 2026-07-19 → 2026-08-16 |

Measured gaps:

- `query_understanding_events`: 7 rows covering 1 run;
- `search_quality_scores`: 402 of 589 runs;
- `ndcg_at_10`: `NULL` in the observed quality rows;
- `final_results.candidate_id` and `canonical_result_id`: `NULL` in the observed rows;
- `query_embeddings`: no rows after 2026-07-29;
- `candidate_embeddings`: 80 rows ending 2026-07-22;
- `summary_intent_daily`, `summary_provider_daily`, `summary_quality_daily`, and `summary_rerank_daily`: 0 rows;
- all `eval_*` tables: 0 rows;
- all `ab_*` tables: 0 rows;
- `analytics_sync_state`: 0 rows;
- judge calibration/rubric/quality-support tables: 0 rows.

These are current coverage and refresh gaps, not reasons to duplicate the web-search funnel.

## 3. Authoritative runtime maps

### 3.1 `web_search`

```text
tools/search.py::web_search
  -> search/service.py::execute_web_search / run_search_core
  -> search/planning.py::plan_search
  -> search/retrieval.py::retrieve_branches
  -> search/ranking.py::rank_and_finalize
  -> search/outcomes.py::submit_search_outcome
```

The runtime creates one immutable `SearchOutcome` per web-search execution. Runtime grains are:

- one `SearchRun`/`SearchOutcome` per execution;
- one `QueryBranch` per planned branch;
- one provider-call row per branch/provider attempt;
- one merged candidate per canonical candidate link;
- one final result per returned rank;
- one row per recorded rerank stage;
- one judgment row only when the asynchronous judge path produces one.

`WebSearchRequest` carries the query, optional seed queries, research goal, result limit, rewrite flag, search options, and reranking instructions. `SearchPlan` carries normalized/relevance queries, understanding, policy version, provider arguments, five rewrite queries, seed queries, and six branches.

The existing `search_runs`/`search_branches`/`provider_calls`/`search_candidates`/`rerank_*`/`final_results` facts remain authoritative for this funnel.

### 3.2 `quick_web_search`

Authoritative modules: `quick_web_search.py::_quick_web_search_impl` and its registered wrapper.

`QuickWebSearchResponse` contains:

- `search_queries`;
- `citations[]` with `title`, `url`, `snippet`, `publish_date`, and `excerpts[]`;
- `total_citations`;
- provider `search_id` and provider `session_id`;
- optional `warnings[]` and `usage[]`.

The wrapper emits request/response/error observability with `tool_call_id`, request arguments, `provider='parallel'`, citations, usage, and measured duration. The runtime has no separate durable internal attempt ID. Do not invent one.

### 3.3 `gemini_search`

Authoritative modules: `tools/ai_search.py::gemini_search` and `search/gemini_search_tool.py::gemini_search_with_grounding`.

`GeminiGroundingResult` contains:

- `query`, `mode`, `answer`, optional `structured_data`;
- `sources[]`, `search_queries[]`, and `url_citations[]`;
- `model_used`;
- `prompt_tokens`, `completion_tokens`, `total_tokens`;
- `grounding_chunks_count` and `web_search_queries_count`;
- `fallback_chain`, `fallback_reason`, and `error`.

Structured mode can execute concurrent `overview` and `deepdive` grounding branches and merge their sources, citations, findings, and token totals. The caller receives one merged output. Individual model attempts do not currently have stable persisted attempt IDs or per-attempt durations; attempt facts are therefore instrumentation-required.

### 3.4 `code_search`

```text
tools/code_search/tool.py::code_search
  -> query.py::build_query_plan
  -> optimization.py::optimize_query_plan
  -> orchestrator.py::execute_code_search
  -> github/sourcegraph/grepapp/exa/docs adapters
  -> ranking.py::rank_hits / verify_regex_hits / compact_hits
  -> reranking.py::rerank_code_hits
  -> models.py::to_public_result
```

`CodeSearchRequest` fields are `query`, `research_goal`, `repositories`, `language`, `path`, `filename`, `extension`, `regexp`, `deep`, `max_results`, `repo_name`, `library_name`, `topic`, `mode`, and `SearchBudget`.

Authoritative `QueryPlan`/`QueryMetadata` fields are:

- `original_query`, `search_text`, `api_query`;
- `variants`, `variant_kinds`, `regex_source`;
- `anchor_terms`, `qualifiers`, `warnings`;
- `source_tokens`, `concept_terms`, `structural_kind`;
- `exa_semantic_query`, `mode`, `backend_channels`, `compiled_queries`.

There are no planner discovery, semantic, or documentation score fields. Do not add them.

`Stats` provides `provider_counts` (returned-hit counts by provider), `request_count`, `hydration_count`, `rerank_count`, `truncated`, `incomplete_providers`, `dropped_count`, estimated output-payload tokens, `elapsed_ms`, and `returned_count`.

`ProviderResponse` is an aggregate provider response containing provider, hits, diagnostics, request count, and metadata. It is not one HTTP attempt and not one query-variant execution. Per-provider duration is not currently carried by it.

`CodeSearchHit` has rich internal provider, query-variant, rank, fragment, symbol, match-span, hydration, score, score-component, reason, evidence-role, and source-metadata fields. `to_public_result()` strips ranking/provider telemetry from the caller-facing payload. Capture internal facts before that projection; never reconstruct them from public output.

`compact_hits()` returns a list and a run-level truncation flag. It does not return a per-hit `compacted` flag. `dropped_count` is the post-rerank-to-final-output difference, not a complete all-stage removal count.

### 3.5 Content and summaries

```text
tools/content.py::get_content
  -> content/summary.py::create_summary
  -> content/summary_backend.py::summarize_with_fallback

tools/content.py::batch_get_content
  -> fetch items without summaries
  -> content/summary.py::create_batch_summaries
  -> content/summary_backend.py::summarize_batch_with_fallback
```

Content events contain retrieval URL/source/backend/status/size/window/pagination fields and parent duration. Batch duration is a parent-operation value and must not be copied to every item.

A single `SummaryOutput` contains summary text, key points, entities, verbatim terms, limitations, and optional source date, but no URL. A `BatchSummaryItem` adds a URL.

The single summary ladder is Gemini primary, Gemini fallback, then Gemma. The batch path tries Gemini batch, Gemma batch, then per-item summaries. Current batch mapping hardcodes `backend='gemini-batch-api'` even when Gemma or per-item fallback produced the result. Per-item fallback has no dedicated summary span. Usage extraction can obtain total tokens, but the compact payload currently emits only model, input, and output tokens.

Summary output facts and model-attempt facts must remain separate.

### 3.6 Adjacent tools

Runtime mapping confirms `discover_links`, `generate_sitemap`, `academic_search`, and `grok_search` all reach the generic `tool_calls` sink but do not drive the `web_search` run-key funnel. The live audit observed no terminal event for the three academic calls or the sitemap call. Keep them in generic coverage views and report request-only calls honestly. Defer typed facts until a separate contract/grain review.

## 4. Proposed DuckDB facts

New tables use `CREATE TABLE IF NOT EXISTS`. Fields unavailable at the table grain are nullable. For facts derived from `tool_calls`, `terminal_event_id` is the source terminal `event_id`; for directly instrumented runtime facts, propagate the same terminal event identity. Child ordinals are ingestion ordinals unless the runtime supplies a native rank.

### 4.1 Existing web-search facts — no duplicate tables

Keep these authoritative:

```text
search_runs
search_branches
provider_calls
search_candidates
rerank_candidates
rerank_stages
final_results
query_embeddings
candidate_embeddings
search_quality_scores
llm_judgments
judge_evaluations
```

Only add columns when a runtime-produced value has an identified writer boundary. Do not create `web_search_runs_v2`, `web_search_providers`, or a parallel result catalog.

### 4.2 `quick_web_search_runs`

**Grain:** one terminal `quick_web_search` event for one parent invocation.

```sql
CREATE TABLE IF NOT EXISTS quick_web_search_runs (
    terminal_event_id       VARCHAR NOT NULL PRIMARY KEY,
    tool_call_id            VARCHAR NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL,
    trace_id                VARCHAR,
    session_id              VARCHAR,
    search_id               VARCHAR,
    provider_session_id     VARCHAR,
    search_queries          VARCHAR[],
    objective               VARCHAR,
    max_results             INTEGER,
    max_chars_total         INTEGER,
    max_chars_per_result    INTEGER,
    client_model            VARCHAR,
    include_domains         VARCHAR[],
    exclude_domains         VARCHAR[],
    after_date              VARCHAR,
    location                VARCHAR,
    max_age_seconds         INTEGER,
    timeout_seconds         DOUBLE,
    disable_cache_fallback  BOOLEAN,
    status                  VARCHAR,
    duration_ms             DOUBLE,
    total_citations         INTEGER,
    warnings                JSON,
    usage                   JSON,
    error_type              VARCHAR,
    error_message           VARCHAR,
    payload_json            JSON
);
```

`provider_session_id` is the provider-returned response session ID; it is distinct from the optional caller input `session_id`. Duration comes from the wrapper event, not the response model.

### 4.3 `quick_web_search_citations`

**Grain:** one citation in one terminal quick-search event.

```sql
CREATE TABLE IF NOT EXISTS quick_web_search_citations (
    terminal_event_id VARCHAR NOT NULL,
    tool_call_id      VARCHAR NOT NULL,
    citation_index    INTEGER NOT NULL,
    title             VARCHAR,
    url               VARCHAR,
    snippet           VARCHAR,
    publish_date      VARCHAR,
    excerpts         VARCHAR[],
    payload_json      JSON,
    PRIMARY KEY (terminal_event_id, citation_index)
);
```

The ordinal is assigned from the returned citation list. It is not described as a provider-native rank unless the provider supplies one.

### 4.4 `gemini_search_runs`

**Grain:** one terminal externally emitted Gemini result, including one merged dual-mode result.

```sql
CREATE TABLE IF NOT EXISTS gemini_search_runs (
    terminal_event_id            VARCHAR NOT NULL PRIMARY KEY,
    tool_call_id                 VARCHAR NOT NULL,
    recorded_at                  TIMESTAMPTZ NOT NULL,
    trace_id                     VARCHAR,
    session_id                   VARCHAR,
    query                        VARCHAR NOT NULL,
    research_goal                VARCHAR,
    structured_output_requested  BOOLEAN,
    mode                         VARCHAR,
    answer                       VARCHAR,
    structured_data              JSON,
    search_queries               VARCHAR[],
    model_used                   VARCHAR,
    prompt_tokens                INTEGER,
    completion_tokens            INTEGER,
    total_tokens                 INTEGER,
    grounding_chunks_count      INTEGER,
    web_search_queries_count    INTEGER,
    fallback_chain               VARCHAR[],
    fallback_reason              VARCHAR,
    status                       VARCHAR,
    duration_ms                  DOUBLE,
    error_message                VARCHAR,
    payload_json                 JSON
);
```

Duration comes from the tool boundary. Do not infer status from answer non-emptiness.

### 4.5 `gemini_search_sources`

**Grain:** one source or URL citation in one terminal Gemini result.

```sql
CREATE TABLE IF NOT EXISTS gemini_search_sources (
    terminal_event_id VARCHAR NOT NULL,
    tool_call_id      VARCHAR NOT NULL,
    source_kind       VARCHAR NOT NULL,
    source_index      INTEGER NOT NULL,
    url               VARCHAR,
    title             VARCHAR,
    source_json       JSON,
    PRIMARY KEY (terminal_event_id, source_kind, source_index)
);
```

`source_kind` preserves the distinction between grounding sources and URL citations. Do not collapse them into a fabricated provider rank.

### 4.6 `gemini_search_attempts`

**Grain:** one actual Gemini model invocation after stable attempt instrumentation exists.

```sql
CREATE TABLE IF NOT EXISTS gemini_search_attempts (
    tool_call_id            VARCHAR NOT NULL,
    attempt_index           INTEGER NOT NULL,
    branch_name             VARCHAR,
    model_requested         VARCHAR,
    model_used              VARCHAR,
    fallback_tier           INTEGER,
    fallback_reason         VARCHAR,
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER,
    total_tokens            INTEGER,
    grounding_chunk_count   INTEGER,
    web_search_query_count  INTEGER,
    status                  VARCHAR,
    duration_ms             DOUBLE,
    error_type              VARCHAR,
    error_message           VARCHAR,
    payload_json            JSON,
    PRIMARY KEY (tool_call_id, attempt_index)
);
```

This table is planned but initially sparse. Current runtime does not persist stable attempt IDs, branch names, or per-attempt duration. Do not create attempts by splitting a merged result or copying outer-span totals.

### 4.7 `code_search_runs`

**Grain:** one terminal `code_search` event for one parent invocation.

```sql
CREATE TABLE IF NOT EXISTS code_search_runs (
    terminal_event_id          VARCHAR NOT NULL PRIMARY KEY,
    tool_call_id               VARCHAR NOT NULL,
    recorded_at                TIMESTAMPTZ NOT NULL,
    trace_id                   VARCHAR,
    session_id                 VARCHAR,
    query                      VARCHAR NOT NULL,
    research_goal              VARCHAR,
    language                   VARCHAR,
    path                       VARCHAR,
    filename                   VARCHAR,
    extension                  VARCHAR,
    regexp_requested           BOOLEAN,
    deep_requested             BOOLEAN,
    max_results_requested      INTEGER,
    repo_name                  VARCHAR,
    library_name               VARCHAR,
    topic                      VARCHAR,
    repository_filters         VARCHAR[],
    planner_original_query     VARCHAR,
    planner_search_text        VARCHAR,
    planner_api_query          VARCHAR,
    planner_mode               VARCHAR,
    planner_structural_kind    VARCHAR,
    planner_exa_semantic_query VARCHAR,
    planner_regex_source       VARCHAR,
    planner_anchor_terms       VARCHAR[],
    planner_concept_terms      VARCHAR[],
    planner_source_tokens      JSON,
    planner_qualifiers         JSON,
    planner_warnings           VARCHAR[],
    planner_backend_channels   VARCHAR[],
    planner_variants           VARCHAR[],
    planner_variant_kinds      VARCHAR[],
    provider_response_count   INTEGER,
    provider_hit_counts        JSON,
    request_count              INTEGER,
    hydration_count            INTEGER,
    rerank_count               INTEGER,
    returned_count             INTEGER,
    repository_count           INTEGER,
    diagnostic_count           INTEGER,
    truncated                 BOOLEAN,
    dropped_count              INTEGER,
    estimated_output_tokens    INTEGER,
    duration_ms                DOUBLE,
    outcome                    VARCHAR,
    error_type                VARCHAR,
    error_message              VARCHAR,
    payload_json               JSON
);
```

Corrections:

- `planner_mode` is valid because `QueryPlan` and `QueryMetadata` expose `mode`;
- planner discovery/semantic/documentation scores are removed because they do not exist;
- `rerank_requested` is removed because the current request has no such field;
- `provider_hit_counts` preserves `Stats.provider_counts` as hit counts, never provider execution counts;
- `estimated_output_tokens` is the bounded output-payload estimate, not LLM token usage;
- `dropped_count` is only the emitted post-rerank/final-compaction difference;
- per-provider and per-phase timing remain `NULL` until instrumented.

### 4.8 `code_search_providers`

**Grain:** one aggregate `ProviderResponse` object in one terminal code-search event.

```sql
CREATE TABLE IF NOT EXISTS code_search_providers (
    terminal_event_id VARCHAR NOT NULL,
    response_index    INTEGER NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL,
    provider          VARCHAR NOT NULL,
    hit_count         INTEGER,
    request_count     INTEGER,
    outcome           VARCHAR,
    compiled_queries  VARCHAR[],
    duration_ms       DOUBLE,
    error_type        VARCHAR,
    error_message     VARCHAR,
    payload_json      JSON,
    PRIMARY KEY (terminal_event_id, response_index)
);
```

This is not one HTTP attempt and not one variant execution. `duration_ms` is instrumentation-required. Runtime provider outcomes are `ok`, `no_hit`, `partial`, or `error`; `skipped` is declared in the type but is not produced by the observed orchestrator path.

### 4.9 `code_search_diagnostics`

**Grain:** one diagnostic emitted by one terminal code-search result.

```sql
CREATE TABLE IF NOT EXISTS code_search_diagnostics (
    terminal_event_id      VARCHAR NOT NULL,
    diagnostic_index       INTEGER NOT NULL,
    recorded_at            TIMESTAMPTZ NOT NULL,
    provider               VARCHAR,
    outcome                VARCHAR,
    failure_kind           VARCHAR,
    message                VARCHAR,
    status_code            INTEGER,
    retry_after_seconds    DOUBLE,
    query                 VARCHAR,
    details               JSON,
    PRIMARY KEY (terminal_event_id, diagnostic_index)
);
```

The index is an ingestion ordinal over the flattened diagnostic list, not a provider request ID.

### 4.10 `code_search_hits`

**Grain:** one final ranked internal `CodeSearchHit` in one terminal invocation.

```sql
CREATE TABLE IF NOT EXISTS code_search_hits (
    terminal_event_id          VARCHAR NOT NULL,
    hit_rank                  INTEGER NOT NULL,
    recorded_at               TIMESTAMPTZ NOT NULL,
    url                       VARCHAR,
    repository                VARCHAR,
    path                      VARCHAR,
    sha                       VARCHAR,
    provider                  VARCHAR,
    query_variant             VARCHAR,
    search_rank               INTEGER,
    result_kind               VARCHAR,
    evidence_role             VARCHAR,
    title                     VARCHAR,
    snippet                   VARCHAR,
    published_date            VARCHAR,
    final_score               DOUBLE,
    score_components           JSON,
    reasons                   VARCHAR[],
    hydrated                  BOOLEAN,
    hydrated_source_truncated BOOLEAN,
    line_start                INTEGER,
    line_end                  INTEGER,
    commit_oid                VARCHAR,
    fragment_count            INTEGER,
    symbol_count              INTEGER,
    match_span_count          INTEGER,
    location_precision        VARCHAR,
    lines_available           BOOLEAN,
    revision_available        BOOLEAN,
    match_data_available      BOOLEAN,
    source_metadata           JSON,
    payload_json              JSON,
    PRIMARY KEY (terminal_event_id, hit_rank)
);
```

`compacted` is intentionally absent: compaction is run-level. Provider, query-variant, score, and provenance fields are populated only before `to_public_result()` strips them; never backfill them from public MCP output.

### 4.11 `code_search_hit_variants`

**Grain:** one contributing planner-variant/provider association for one final hit.

```sql
CREATE TABLE IF NOT EXISTS code_search_hit_variants (
    terminal_event_id  VARCHAR NOT NULL,
    hit_rank           INTEGER NOT NULL,
    association_index  INTEGER NOT NULL,
    variant_index      INTEGER,
    provider            VARCHAR,
    query_variant      VARCHAR,
    search_rank        INTEGER,
    PRIMARY KEY (terminal_event_id, hit_rank, association_index)
);
```

A merged hit may have multiple providers and variants. Adapter loops do not currently preserve a reliable planner-variant index through the full merge, so association rows and `variant_index` are instrumentation-required. Do not derive them from `provider_hit_counts`.

### 4.12 `code_search_query_variants`

**Grain:** one planner variant in one terminal invocation.

```sql
CREATE TABLE IF NOT EXISTS code_search_query_variants (
    terminal_event_id VARCHAR NOT NULL,
    variant_index     INTEGER NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL,
    query_text        VARCHAR NOT NULL,
    variant_kind      VARCHAR,
    PRIMARY KEY (terminal_event_id, variant_index)
);
```

`query_text` and `variant_kind` come from `QueryPlan.variants` and `QueryPlan.variant_kinds`. Do not add `assigned_providers`: compiled provider-query metadata does not preserve a reliable variant-index mapping.

### 4.13 `code_search_repositories`

**Grain:** one discovered `RepoCandidate` in one terminal invocation.

```sql
CREATE TABLE IF NOT EXISTS code_search_repositories (
    terminal_event_id  VARCHAR NOT NULL,
    repository_index   INTEGER NOT NULL,
    recorded_at        TIMESTAMPTZ NOT NULL,
    name_with_owner    VARCHAR,
    url                VARCHAR,
    description        VARCHAR,
    stars              INTEGER,
    forks              INTEGER,
    pushed_at          VARCHAR,
    language           VARCHAR,
    topics             VARCHAR[],
    license_spdx_id    VARCHAR,
    homepage_url       VARCHAR,
    default_branch     VARCHAR,
    head_oid           VARCHAR,
    archived           BOOLEAN,
    fork               BOOLEAN,
    discovery_rank     INTEGER,
    discovery_score    DOUBLE,
    discovery_queries  VARCHAR[],
    proof_hits         INTEGER,
    proof_paths        VARCHAR[],
    proof_providers    VARCHAR[],
    verified           BOOLEAN,
    payload_json       JSON,
    PRIMARY KEY (terminal_event_id, repository_index)
);
```

`repository_index` is an ingestion ordinal. `discovery_score` is nullable and must be populated only when the runtime supplies it.

### 4.14 `code_search_rerank`

**Grain:** one cloud rerank outcome for one terminal invocation when the path is reached.

```sql
CREATE TABLE IF NOT EXISTS code_search_rerank (
    terminal_event_id   VARCHAR NOT NULL PRIMARY KEY,
    recorded_at         TIMESTAMPTZ NOT NULL,
    provider             VARCHAR,
    model                VARCHAR,
    input_count         INTEGER,
    output_count        INTEGER,
    reranked_count      INTEGER,
    status               VARCHAR,
    diagnostic_outcome   VARCHAR,
    diagnostic_message   VARCHAR,
    duration_ms          DOUBLE,
    payload_json         JSON
);
```

This measures execution behavior, not a causal reranked-versus-not-reranked experiment. The current path attempts cloud reranking when hits exist and fails open. Duration is instrumentation-required at the orchestration boundary.

### 4.15 `content_operations`

**Grain:** one terminal `get_content` or `batch_get_content` event.

```sql
CREATE TABLE IF NOT EXISTS content_operations (
    terminal_event_id VARCHAR NOT NULL PRIMARY KEY,
    tool_call_id      VARCHAR NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL,
    trace_id          VARCHAR,
    session_id        VARCHAR,
    tool_name         VARCHAR NOT NULL,
    input_count       INTEGER,
    output_count      INTEGER,
    duration_ms       DOUBLE,
    status             VARCHAR,
    error_type        VARCHAR,
    error_message     VARCHAR,
    payload_json      JSON
);
```

The operation row owns parent duration and status. It prevents batch duration from being duplicated across item rows.

### 4.16 `content_fetches`

**Grain:** one emitted content item in one terminal event; `item_index=0` for a single item.

```sql
CREATE TABLE IF NOT EXISTS content_fetches (
    terminal_event_id       VARCHAR NOT NULL,
    tool_call_id            VARCHAR NOT NULL,
    item_index              INTEGER NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL,
    input_url               VARCHAR,
    normalized_url          VARCHAR,
    fetched_url             VARCHAR,
    source_type             VARCHAR,
    fetch_backend           VARCHAR,
    status                  VARCHAR,
    content_length          INTEGER,
    page_char_count         INTEGER,
    word_count              INTEGER,
    window_offset           INTEGER,
    window_length           INTEGER,
    window_returned_chars   INTEGER,
    window_total_chars      INTEGER,
    window_has_more         BOOLEAN,
    window_next_offset      INTEGER,
    item_duration_ms        DOUBLE,
    payload_json            JSON,
    PRIMARY KEY (terminal_event_id, item_index)
);
```

`item_duration_ms` is nullable because the current batch path supplies parent duration, not per-item duration.

### 4.17 `content_summaries`

**Grain:** one final summary output item in one terminal event, not one model attempt.

```sql
CREATE TABLE IF NOT EXISTS content_summaries (
    terminal_event_id          VARCHAR NOT NULL,
    tool_call_id               VARCHAR NOT NULL,
    item_index                 INTEGER NOT NULL,
    recorded_at                TIMESTAMPTZ NOT NULL,
    normalized_url             VARCHAR,
    focus_query                VARCHAR,
    input_chars                INTEGER,
    source_url_count           INTEGER,
    is_batch                   BOOLEAN,
    batch_size                 INTEGER,
    is_stub                    BOOLEAN,
    backend                    VARCHAR,
    model_requested            VARCHAR,
    model_used                 VARCHAR,
    fallback_attempted         BOOLEAN,
    fallback_tier              INTEGER,
    input_tokens               INTEGER,
    output_tokens              INTEGER,
    total_tokens               INTEGER,
    summary_length_chars       INTEGER,
    key_points_count           INTEGER,
    important_entities_count   INTEGER,
    verbatim_terms_count       INTEGER,
    limitations_count          INTEGER,
    source_date                VARCHAR,
    status                     VARCHAR,
    error_type                VARCHAR,
    error_message              VARCHAR,
    duration_ms                DOUBLE,
    payload_json               JSON,
    PRIMARY KEY (terminal_event_id, item_index)
);
```

Corrections:

- no `summary_id` or `content_fetch_id` is claimed because neither is a current runtime identifier;
- a single summary URL is nullable because `SummaryOutput` has no URL field;
- `model_used` and `backend` are nullable for stubs and must not trust the current hardcoded batch backend value;
- batch duration and batch tokens are not copied to each item;
- `total_tokens` remains `NULL` until the extracted value is explicitly persisted;
- output-shape counts are descriptive signals, not quality judgments;
- `fallback_attempted` and `fallback_tier` remain nullable unless an explicit final-path field or attempt instrumentation supplies them.

`item_index` is a writer-assigned ordinal over the emitted item list, not a runtime item identity.

### 4.18 `content_summary_attempts`

**Grain:** one actual summary backend/model attempt after the relevant boundary is instrumented.

```sql
CREATE TABLE IF NOT EXISTS content_summary_attempts (
    tool_call_id       VARCHAR NOT NULL,
    item_index         INTEGER,
    attempt_index      INTEGER NOT NULL,
    recorded_at        TIMESTAMPTZ NOT NULL,
    is_batch           BOOLEAN,
    batch_size         INTEGER,
    backend             VARCHAR,
    model_requested     VARCHAR,
    model_used          VARCHAR,
    fallback_tier       INTEGER,
    source_url_count    INTEGER,
    input_chars         INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    total_tokens        INTEGER,
    duration_ms         DOUBLE,
    status              VARCHAR,
    error_type          VARCHAR,
    error_message       VARCHAR,
    payload_json        JSON,
    PRIMARY KEY (tool_call_id, attempt_index)
);
```

Current gaps are explicit: fallback attempts are not individually written, per-item batch fallback has no dedicated span, batch input characters/tokens are absent, and `llm_usage_fields()` drops `total_tokens`. `attempt_index` is a writer-assigned ordinal across actual attempts for one parent invocation; `item_index` is optional context for item-level attempts. No attempt rows may be inferred by duplicating outer-span values.


## 5. Analytical views

Views must describe the signal they contain. Execution counts are not quality. Output-shape counts are not summary quality. Focus comparisons are not causal effectiveness.

### 5.1 Cross-tool views

#### `vw_tool_call_coverage`

From `tool_calls`, report per tool and status:

- request, response, and error event counts;
- distinct parent `tool_call_id` count;
- request-only count;
- terminal-event rate;
- average and p95 response duration where supplied;
- first and last observed timestamps.

Request-only academic or sitemap calls remain request-only.

#### `vw_tool_call_linkage_gaps`

Report null rates and distinct counts for `tool_call_id`, `trace_id`, `session_id`, and request fingerprints by tool. Do not infer a session, trace, or user from another identifier.

#### `vw_web_search_tool_linkage`

Left join generic web-search events to `search_runs` on `tool_call_id`, retaining unmatched events and the existing `run_key` when present. This is a linkage diagnostic, not a backfill.

### 5.2 Existing web-search views

Preserve and use the existing web-search views for run funnels, provider reliability, rewrite attribution, rerank timelines, candidate funnels, judged quality, bad-case queues, and result diagnostics.

Use `search_runs`/`search_branches`/`provider_calls` denominators for web-search funnel questions. Do not substitute generic `tool_calls` counts where `run_key` is authoritative.

### 5.3 Quick-search views

#### `vw_quick_web_search_performance`

Aggregate typed quick-search terminal rows by provider, client model, request shape, and observed status. Report call count, citation count, warning count, duration, and usage presence. The runtime locks the provider mode to Parallel advanced search; do not invent a session-mode field.

#### `vw_quick_web_search_citation_sources`

Aggregate citations by domain, publish-date presence, and count. Preserve the distinction between returned citations and later `get_content` reads. Do not call citations relevant without a judgment signal.

### 5.4 Gemini-search views

#### `vw_gemini_search_performance`

Aggregate by `model_used`, `mode`, and status. Report answer presence, source count, grounding chunks, grounding-query count, known token values, fallback-chain presence, and duration.

#### `vw_gemini_search_fallbacks`

Use `gemini_search_attempts` only for populated instrumented attempts. Until then, expose final-result fallback-chain presence from `gemini_search_runs` separately and label it as incomplete attempt coverage.

#### `vw_gemini_search_sources`

Report source/citation counts and URLs while retaining `source_kind`. Do not infer provider yield or source relevance from source count.

### 5.5 Code-search views

#### `vw_code_search_provider_yield`

Use aggregate `code_search_providers` rows as the denominator. Report hit count, request count, runtime outcome, and latency only where response timing exists. Do not interpret `provider_hit_counts` as provider execution count.

#### `vw_code_search_hit_sources`

Use internal hit rows and source/variant bridge rows when populated. Report provider contribution and evidence signals, not judged relevance.

#### `vw_code_search_variant_effectiveness`

Use populated `code_search_hit_variants` associations. Report runs using a variant, hits with a variant, top-rank presence, average final score, repositories, and provider combinations. This is engine diagnostics, not causal superiority evidence.

#### `vw_code_search_rerank_execution`

Report rerank status, provider/model, input/output/reranked counts, diagnostic outcomes, and latency. Do not compare with a nonexistent non-reranked control path.

#### `vw_code_search_diagnostic_patterns`

Group all diagnostic outcomes, including partial, error, no-hit, and validation diagnostics. Do not filter to errors when analyzing incomplete retrieval.

#### `vw_code_search_repository_discovery`

Report repository rows by language, verification, archive/fork state, proof counts, and observed discovery score when present. Keep nullable fields out of denominators that require them.

#### `vw_code_search_score_component_distribution`

Use `TRY_CAST` for JSON score components. Report component distributions by provider, result kind, and evidence role. This describes the scoring formula; it does not establish feature importance.

### 5.6 Content and summary views

#### `vw_content_fetch_performance`

Aggregate `content_operations` by tool, status, and date; use `content_fetches` for fetch backend, source type, and content-size breakdowns. Report parent duration, output count, blocked/error/partial rates, and item fields where available.

#### `vw_content_summary_output_signals`

Aggregate final output rows by non-NULL model/backend. Report summary length, key-point/entity/verbatim/limitation counts, source-date capture, stub count, and status. Name these output-shape signals, not quality.

#### `vw_content_summary_attempt_performance`

Aggregate actual attempt rows by backend/model, attempt kind, status, duration, and nullable token usage. Empty or sparse attempt data must remain visible.

#### `vw_content_summary_batch_vs_single`

Use operation/attempt rows for job-level duration and tokens, and output rows for item-level signals. Never sum a batch parent duration once per item.

#### `vw_content_summary_fallbacks`

Use attempt rows for fallback counts and latency. Until attempt instrumentation exists, expose only final backend/fallback-chain signals and label them incomplete.

#### `vw_content_summary_focus_comparison`

Compare focused and unfocused output-shape signals descriptively. Do not call this causal effectiveness.

#### `vw_content_summary_daily_tokens`

Aggregate only known token values. `NULL` means unavailable; never convert unavailable token values to zero for cost or token claims. Do not call this a cost view without actual monetary pricing.

## 6. Field provenance matrix

| Area | Field/metric | Evidence | Treatment |
|---|---|---|---|
| All tools | `tool_call_id` | Generic wrapper event | Parent invocation key |
| All tools | `terminal_event_id` | `tool_calls.event_id` | Terminal/event-row key for event-derived facts |
| All tools | `trace_id`/`session_id` | Wrapper/runtime context | Nullable; expose linkage gaps |
| Web search | `run_key`, branches, calls, candidates, rerank, final results | `SearchOutcome` and existing DuckDB facts | Preserve existing model |
| Web search | tool-call linkage | `search_runs.tool_call_id` plus generic events | Join only when present |
| Web search | rewrite, intent, branch roles | `SearchPlan`/`SearchOutcome` | Existing funnel fields/writer boundary |
| Quick search | citations and metadata | `QuickWebSearchResponse` | Typed run/citation facts |
| Quick search | duration/provider | Wrapper response event | Typed run fact |
| Gemini | merged answer, sources, tokens, grounding counts | `GeminiGroundingResult` | Typed output/source facts |
| Gemini | individual model attempts | Internal fallback/dual loop | Instrumentation-required attempt facts |
| Code search | planner variants, kinds, mode, qualifiers | `QueryPlan`/`QueryMetadata` | Typed run/variant facts |
| Code search | provider hit counts | `Stats.provider_counts` | JSON hit-count map only |
| Code search | total duration | `Stats.elapsed_ms` and wrapper event | Run duration |
| Code search | provider duration | Not in `ProviderResponse` | Nullable until orchestrator instrumentation |
| Code search | rich ranking/provenance | Internal `CodeSearchHit` before public projection | Capture before `to_public_result()` |
| Code search | all contributing variants | Merge metadata plus adapter propagation | Bridge table; instrumentation-required until preserved |
| Code search | compaction | Run-level `compact_hits()` result | Store `truncated`/`dropped_count`; no hit flag |
| Content | fetch backend/status/size/window | Content terminal events | Operations and item facts |
| Summary | final output fields | `SummaryOutput`/`BatchSummaryItem` | Output table |
| Summary | single output URL | Not in `SummaryOutput` | Nullable; use captured content item only |
| Summary | model/backend | Single payload; batch metadata currently inconsistent | Nullable; attempt rows canonical after instrumentation |
| Summary | fallback attempted/tier | Not stable on every final path | Nullable until explicitly emitted or derived from attempts |
| Summary | total tokens | Extracted but dropped from compact payload | Nullable until persisted |
| Adjacent tools | response/attempt facts | Generic `tool_calls` only | Separate future proposal after contract evidence |

## 7. DuckDB migration and writer rules

Use DuckDB-native additive migrations:

- `CREATE TABLE IF NOT EXISTS` for new facts;
- `ALTER TABLE ... ADD COLUMN` for additive changes to existing facts;
- `CREATE OR REPLACE VIEW` for views;
- explicit `TIMESTAMPTZ`, `DATE`, `BOOLEAN`, `VARCHAR`, `VARCHAR[]`, and `JSON` types;
- `NULL` for unavailable values, never fabricated zeroes or empty strings;
- deterministic list ordinals only where the source exposes a list or the writer documents an ingestion ordinal;
- idempotent writes keyed by terminal event plus child ordinal, or by parent invocation plus explicitly instrumented attempt index;
- JSON retention for provider-specific shapes that are not stable enough to flatten.

DuckDB documentation used:

- `ALTER TABLE ... ADD COLUMN`: <https://duckdb.org/docs/stable/sql/statements/alter_table>
- `CREATE TABLE` and constraints: <https://duckdb.org/docs/stable/sql/statements/create_table>
- `CREATE VIEW`: <https://duckdb.org/docs/stable/sql/statements/create_view>
- DuckDB data types, including LIST and JSON: <https://duckdb.org/docs/stable/sql/data_types/overview>

Primary keys document intended uniqueness but are not a substitute for idempotent writer behavior or source-level deduplication.

### Existing web-search lifecycle

Do not add a second web-search writer. Preserve the runtime's existing outcome boundary and run-key child writes. Add only source-backed columns after confirming the runtime value and writer boundary.

### New typed writers

Add writers at these boundaries:

- quick terminal event → `quick_web_search_runs` and citations;
- Gemini terminal result → `gemini_search_runs` and sources;
- Gemini internal loops → attempts only after attempt instrumentation;
- code-search terminal/internal result boundary → parent, provider, diagnostic, hit, repository, rerank, and variant facts before public projection;
- content terminal events → operations and fetch items;
- final summary outputs → `content_summaries`;
- each actual summary backend/model loop → `content_summary_attempts` after instrumentation.

Parent facts should survive secondary child-write failures. A child write failure must not turn a successful parent operation into a fabricated error.

### View and refresh lifecycle

Register new views in the existing view-registration lifecycle. Existing daily summary tables are empty; creating views does not mean materialized summaries are refreshed. Add an explicit refresh step or command for materialized rollups and expose refresh failures. Do not report a rollup as populated until the database contains rows.

Remote synchronization is optional follow-up work. If added later, synchronize local typed tables using the same idempotent keys and rebuild remote views after synchronization. Do not add MotherDuck-only dimensions or change local grains for a remote target.

## 8. Analytical questions

### Web-search planning and quality

- Which rewrite/branch roles increase candidate coverage?
- Which providers return empty, timeout, incomplete, or error outcomes?
- Where do candidates disappear between retrieval, merge, rerank, and final output?
- Which rerank stages compress or reorder candidates?
- Which judged results are bad cases by intent, provider overlap, or provenance?

Use existing web-search facts and run-key denominators.

### Quick search

- How many calls return citations or warnings?
- What are duration and citation-count distributions by date/request shape?
- Which returned domains are later read through `get_content`?

These are execution/source-coverage questions, not relevance judgments.

### Gemini grounded search

- Which models/modes/fallback chains produce outputs and grounding sources?
- How many tokens and grounding queries are known?
- How often are results merged from dual mode?
- What individual fallback behavior appears after attempt instrumentation?

Do not infer model quality from answer length or source count.

### Code search

- Which aggregate provider responses return hits, partial results, no hits, or errors?
- Which internal evidence roles, scores, and providers survive final ranking?
- Which planned variants contribute after provenance instrumentation?
- How often do hydration, regex verification, cloud reranking, and compaction occur?
- Which diagnostics cluster by provider or time?

### Content and summaries

- Which fetch backends fail, block, or return partial windows?
- How often are final summaries present, stubbed, or missing per batch item?
- Which output-shape signals differ by model, backend, batch mode, or focus query?
- Which token/duration values are known at operation, item, and attempt grains?
- How often does fallback activate once attempt rows exist?

## 9. Rollout order

1. Add cross-tool coverage and linkage views over existing `tool_calls`.
2. Preserve the existing web-search funnel and add only verified additive columns.
3. Add quick-search terminal and citation facts.
4. Add Gemini terminal and source facts; instrument attempts before relying on attempt views.
5. Add code-search parent/provider/diagnostic/hit/repository/rerank/variant facts before public projection.
6. Preserve all contributing code-search variant provenance before enabling variant-effectiveness reporting.
7. Add content operation, fetch-item, and final-summary output facts.
8. Add summary attempt rows at single, batch, Gemma, and per-item fallback boundaries; correct batch backend labeling before treating backend as authoritative.
9. Register descriptive views and wire explicit materialized-summary refreshes.
10. Run read-only DuckDB checks and verify every view denominator against its declared grain.

## 10. Explicit non-goals

Do not add:

- a replacement web-search funnel or generic `mcp_sessions` model;
- a raw event warehouse in addition to `tool_calls`;
- speculative pricing, model dimensions, prompt revisions, or ingestion dimensions;
- click/CTR/MRR facts before the client emits impression/click events;
- code-search embeddings, full fragment tables, full symbol tables, or a cross-run result catalog;
- per-provider code-search latency before the orchestrator emits it;
- per-attempt Gemini or summary rows before stable attempt boundaries exist;
- summary quality judgments before a summary judge exists;
- causal claims from rerank, focus-query, provider-yield, source-count, or output-shape views;
- backfilled session IDs, trace IDs, run keys, URLs, tokens, costs, or outcomes absent from source events.

The target is a source-correct, grain-correct DuckDB analytics extension for the full search application: preserve the existing web-search quality model as the central funnel, and make quick search, Gemini search, code search, content retrieval, and summaries inspectable without inventing values.
