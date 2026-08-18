"""PROTOTYPE — wipe me. In-memory DuckDB model for search analytics grains.

The prototype exists to validate facts, identities, lineage, and analytical
views. It does not touch the persistent analytics database or production
writers.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import duckdb

_CANON_PATH = Path(__file__).resolve().parents[1] / "utils" / "url_canonicalize.py"
_spec = importlib.util.spec_from_file_location("_proto_url_canonicalize", _CANON_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
canonicalize = _mod.canonicalize_url

ViewName = Literal[
    "invocations",
    "funnel",
    "trajectory",
    "branch",
    "provider",
    "rewrite",
    "usefulness",
    "judgments",
    "followups",
    "quality",
    "embeddings",
    "cost",
]
VIEWS: tuple[ViewName, ...] = (
    "invocations",
    "funnel",
    "trajectory",
    "branch",
    "provider",
    "rewrite",
    "usefulness",
    "judgments",
    "followups",
    "quality",
    "embeddings",
    "cost",
)

TABLES = (
    "search_runs",
    "query_variants",
    "search_branches",
    "provider_calls",
    "result_catalog",
    "provider_results",
    "search_candidates",
    "rerank_stage_executions",
    "candidate_stage_events",
    "final_results",
    "tool_events",
    "tool_output_items",
    "content_fetches",
    "llm_calls",
    "judgment_facets",
    "embedding_models",
    "query_embeddings",
    "candidate_embeddings",
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

VSS_RECOMMENDATION = {
    "use_now": "exact ARRAY cosine scans; the live database has only hundreds of vectors",
    "future_hnsw_use": (
        "interactive top-k similar historical queries or candidates after row-count/latency "
        "benchmarks justify an approximate index"
    ),
    "do_not_use_for": (
        "within-run reranking, canonical identity, provider/rewrite attribution, funnel views, "
        "or all-pairs drift analytics"
    ),
    "operational_gate": (
        "keep HNSW rebuildable and in-memory; DuckDB still marks persistent VSS indexes "
        "experimental and outside memory_limit"
    ),
}

SCHEMA_SQL = """
CREATE TABLE search_runs (
    run_key VARCHAR PRIMARY KEY,
    tool_call_id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    original_query VARCHAR NOT NULL,
    normalized_query VARCHAR NOT NULL,
    intent VARCHAR,
    requested_results INTEGER,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'success', 'error')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE query_variants (
    variant_id VARCHAR PRIMARY KEY,
    run_key VARCHAR NOT NULL,
    parent_variant_id VARCHAR,
    variant_order INTEGER NOT NULL,
    variant_role VARCHAR NOT NULL CHECK (variant_role IN ('original', 'rewrite', 'expansion')),
    query_text VARCHAR NOT NULL,
    proposed BOOLEAN NOT NULL,
    selected BOOLEAN NOT NULL,
    executed BOOLEAN NOT NULL,
    skip_reason VARCHAR,
    UNIQUE (run_key, variant_order)
);

CREATE TABLE search_branches (
    branch_id VARCHAR PRIMARY KEY,
    run_key VARCHAR NOT NULL,
    variant_id VARCHAR NOT NULL,
    branch_order INTEGER NOT NULL,
    branch_role VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (run_key, branch_order)
);

CREATE TABLE provider_calls (
    provider_call_id VARCHAR PRIMARY KEY,
    run_key VARCHAR NOT NULL,
    branch_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    request_query VARCHAR NOT NULL,
    requested_results INTEGER NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('success', 'empty', 'error')),
    latency_ms DOUBLE,
    error_type VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE result_catalog (
    canonical_result_id VARCHAR PRIMARY KEY,
    canonical_url VARCHAR NOT NULL UNIQUE,
    domain VARCHAR NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE provider_results (
    provider_result_id VARCHAR PRIMARY KEY,
    provider_call_id VARCHAR NOT NULL,
    run_key VARCHAR NOT NULL,
    branch_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    provider_rank INTEGER NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    raw_url VARCHAR NOT NULL,
    title VARCHAR,
    snippet VARCHAR,
    raw_score DOUBLE,
    is_eligible BOOLEAN NOT NULL,
    rejection_reason VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (provider_call_id, provider_rank)
);

CREATE TABLE search_candidates (
    run_key VARCHAR NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    merge_rank INTEGER NOT NULL,
    rrf_score DOUBLE,
    bm25_score DOUBLE,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_key, canonical_result_id),
    UNIQUE (run_key, merge_rank)
);

CREATE TABLE rerank_stage_executions (
    stage_execution_id VARCHAR PRIMARY KEY,
    run_key VARCHAR NOT NULL,
    stage_order INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    stage_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('success', 'skipped', 'error')),
    fail_open BOOLEAN NOT NULL,
    input_count INTEGER,
    output_count INTEGER,
    model_id VARCHAR,
    threshold DOUBLE,
    duration_ms DOUBLE,
    error_type VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_key, stage_order, attempt),
    CHECK (status != 'skipped' OR output_count IS NULL)
);

CREATE TABLE candidate_stage_events (
    stage_execution_id VARCHAR NOT NULL,
    run_key VARCHAR NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    entered BOOLEAN NOT NULL,
    survived BOOLEAN NOT NULL,
    rank_before INTEGER,
    rank_after INTEGER,
    score_before DOUBLE,
    score_after DOUBLE,
    score_name VARCHAR,
    removal_reason VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (stage_execution_id, canonical_result_id),
    CHECK (survived = FALSE OR entered = TRUE)
);

CREATE TABLE final_results (
    run_key VARCHAR NOT NULL,
    final_rank INTEGER NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    title VARCHAR,
    final_score DOUBLE,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_key, final_rank),
    UNIQUE (run_key, canonical_result_id)
);

CREATE TABLE tool_events (
    event_id VARCHAR PRIMARY KEY,
    tool_call_id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    run_key VARCHAR,
    tool_name VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN ('request', 'response', 'error')),
    error_type VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE tool_output_items (
    output_item_id VARCHAR PRIMARY KEY,
    tool_call_id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    run_key VARCHAR,
    item_type VARCHAR NOT NULL,
    item_rank INTEGER NOT NULL,
    canonical_result_id VARCHAR,
    raw_url VARCHAR,
    title VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (tool_call_id, item_type, item_rank)
);

CREATE TABLE content_fetches (
    fetch_id VARCHAR PRIMARY KEY,
    fetch_tool_call_id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    run_key VARCHAR,
    source_output_item_id VARCHAR,
    canonical_result_id VARCHAR NOT NULL,
    input_url VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('success', 'partial', 'error')),
    content_chars INTEGER,
    fetch_backend VARCHAR,
    cache_status VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE llm_calls (
    llm_call_id VARCHAR PRIMARY KEY,
    run_key VARCHAR,
    tool_call_id VARCHAR NOT NULL,
    purpose VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd DOUBLE,
    latency_ms DOUBLE,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE judgment_facets (
    judgment_id VARCHAR PRIMARY KEY,
    run_key VARCHAR,
    tool_call_id VARCHAR NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    facet_name VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    judge_model_id VARCHAR NOT NULL,
    rubric_version VARCHAR NOT NULL,
    reasoning VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (tool_call_id, canonical_result_id, facet_name, judge_model_id, rubric_version)
);

CREATE TABLE embedding_models (
    model_id VARCHAR PRIMARY KEY,
    dimensions INTEGER NOT NULL,
    distance_metric VARCHAR NOT NULL,
    normalized BOOLEAN NOT NULL,
    text_recipe_version VARCHAR NOT NULL
);

CREATE TABLE query_embeddings (
    run_key VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    embedded_text_hash VARCHAR NOT NULL,
    embedding FLOAT[3] NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_key, model_id)
);

CREATE TABLE candidate_embeddings (
    run_key VARCHAR NOT NULL,
    canonical_result_id VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    embedded_text_hash VARCHAR NOT NULL,
    embedding FLOAT[3] NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_key, canonical_result_id, model_id)
);

CREATE VIEW vw_tool_invocations AS
WITH folded AS (
    SELECT
        tool_call_id,
        any_value(session_id) AS session_id,
        any_value(run_key) AS run_key,
        any_value(tool_name) AS tool_name,
        count(*) FILTER (WHERE event_type = 'request') AS request_events,
        count(*) FILTER (WHERE event_type IN ('response', 'error')) AS terminal_events,
        bool_or(event_type = 'response') AS has_response,
        bool_or(event_type = 'error') AS has_error,
        min(recorded_at) FILTER (WHERE event_type = 'request') AS started_at,
        max(recorded_at) FILTER (WHERE event_type IN ('response', 'error')) AS completed_at,
        arg_max(error_type, recorded_at) FILTER (WHERE event_type = 'error') AS error_type
    FROM tool_events
    GROUP BY tool_call_id
)
SELECT
    *,
    CASE
        WHEN request_events = 0 THEN 'orphan_terminal'
        WHEN request_events > 1 THEN 'duplicate_request'
        WHEN terminal_events = 0 THEN 'incomplete'
        WHEN terminal_events > 1 THEN 'conflicting_terminal'
        WHEN has_error THEN 'error'
        ELSE 'success'
    END AS lifecycle_status,
    CASE
        WHEN started_at IS NOT NULL AND completed_at IS NOT NULL
        THEN date_diff('millisecond', started_at, completed_at)
    END AS duration_ms
FROM folded;

CREATE VIEW vw_run_stage_funnel AS
WITH provider_counts AS (
    SELECT
        run_key,
        count(*)::INTEGER AS provider_raw,
        count(DISTINCT canonical_result_id)::INTEGER AS provider_unique,
        count(DISTINCT canonical_result_id) FILTER (WHERE is_eligible)::INTEGER AS eligible
    FROM provider_results
    GROUP BY run_key
), merge_counts AS (
    SELECT run_key, count(*)::INTEGER AS merged
    FROM search_candidates
    GROUP BY run_key
), final_counts AS (
    SELECT run_key, count(*)::INTEGER AS final_count
    FROM final_results
    GROUP BY run_key
), latest_effective_stage AS (
    SELECT run_key, output_count
    FROM rerank_stage_executions
    WHERE status != 'skipped'
    QUALIFY row_number() OVER (
        PARTITION BY run_key
        ORDER BY stage_order DESC, attempt DESC
    ) = 1
), per_run AS (
    SELECT
        r.run_key,
        r.status AS run_status,
        coalesce(p.provider_raw, 0) AS provider_raw,
        coalesce(p.provider_unique, 0) AS provider_unique,
        coalesce(p.eligible, 0) AS eligible,
        coalesce(m.merged, 0) AS merged,
        coalesce(e.output_count, m.merged, 0) AS final_input,
        coalesce(f.final_count, 0) AS final_count
    FROM search_runs r
    LEFT JOIN provider_counts p USING (run_key)
    LEFT JOIN merge_counts m USING (run_key)
    LEFT JOIN latest_effective_stage e USING (run_key)
    LEFT JOIN final_counts f USING (run_key)
), synthetic AS (
    SELECT run_key, -40 AS stage_order, 'provider_raw' AS stage_name, 'success' AS status,
           provider_raw AS input_count, provider_raw AS output_count
    FROM per_run
    UNION ALL
    SELECT run_key, -30, 'provider_unique', 'success', provider_raw, provider_unique
    FROM per_run
    UNION ALL
    SELECT run_key, -20, 'eligible', 'success', provider_unique, eligible
    FROM per_run
    UNION ALL
    SELECT run_key, -10, 'merge', 'success', eligible, merged
    FROM per_run
    UNION ALL
    SELECT run_key, 1000, 'final',
           CASE WHEN run_status = 'running' THEN 'pending' ELSE run_status END,
           final_input, final_count
    FROM per_run
), stages AS (
    SELECT run_key, stage_order, stage_name, status, input_count, output_count
    FROM rerank_stage_executions
)
SELECT * FROM synthetic
UNION ALL
SELECT * FROM stages;

CREATE VIEW vw_run_funnel AS
SELECT
    run_key,
    max(output_count) FILTER (WHERE stage_name = 'provider_raw') AS provider_raw,
    max(output_count) FILTER (WHERE stage_name = 'provider_unique') AS provider_unique,
    max(output_count) FILTER (WHERE stage_name = 'eligible') AS eligible,
    max(output_count) FILTER (WHERE stage_name = 'merge') AS merged,
    max(output_count) FILTER (WHERE stage_name = 'bi_encoder') AS bi_output,
    max(status) FILTER (WHERE stage_name = 'bi_encoder') AS bi_status,
    max(output_count) FILTER (WHERE stage_name = 'cross_encoder') AS cross_output,
    max(status) FILTER (WHERE stage_name = 'cross_encoder') AS cross_status,
    max(output_count) FILTER (WHERE stage_name = 'rankllm') AS rankllm_output,
    max(status) FILTER (WHERE stage_name = 'rankllm') AS rankllm_status,
    max(output_count) FILTER (WHERE stage_name = 'final') AS final_count
FROM vw_run_stage_funnel
GROUP BY run_key;

CREATE VIEW vw_candidate_trajectory AS
WITH discovery AS (
    SELECT
        run_key,
        canonical_result_id,
        count(DISTINCT provider) AS discovering_providers,
        count(DISTINCT branch_id) AS discovering_branches,
        min(provider_rank) AS best_provider_rank
    FROM provider_results
    WHERE is_eligible
    GROUP BY run_key, canonical_result_id
)
SELECT
    c.run_key,
    c.canonical_result_id,
    rc.canonical_url,
    d.discovering_providers,
    d.discovering_branches,
    d.best_provider_rank,
    c.merge_rank,
    c.rrf_score,
    s.stage_execution_id,
    s.stage_order,
    s.stage_name,
    s.status AS stage_status,
    s.fail_open,
    e.entered,
    e.survived,
    e.rank_before,
    e.rank_after,
    e.score_after,
    e.score_name,
    e.removal_reason,
    f.final_rank
FROM search_candidates c
JOIN result_catalog rc USING (canonical_result_id)
LEFT JOIN discovery d USING (run_key, canonical_result_id)
JOIN rerank_stage_executions s USING (run_key)
LEFT JOIN candidate_stage_events e
  ON e.stage_execution_id = s.stage_execution_id
 AND e.canonical_result_id = c.canonical_result_id
LEFT JOIN final_results f USING (run_key, canonical_result_id);

CREATE VIEW vw_provider_contribution AS
WITH discovered AS (
    SELECT DISTINCT run_key, provider, canonical_result_id
    FROM provider_results
), multiplicity AS (
    SELECT run_key, canonical_result_id, count(*) AS provider_count
    FROM discovered
    GROUP BY ALL
), eligible AS (
    SELECT DISTINCT run_key, provider, canonical_result_id
    FROM provider_results
    WHERE is_eligible
), judged AS (
    SELECT DISTINCT run_key, canonical_result_id
    FROM judgment_facets
    WHERE facet_name = 'relevance'
)
SELECT
    d.run_key,
    d.provider,
    count(*) AS discovered_unique,
    count(*) FILTER (WHERE m.provider_count = 1) AS exclusive_candidates,
    sum(1.0 / m.provider_count) AS fractional_discovery_credit,
    count(e.canonical_result_id) AS eligible_candidates,
    count(c.canonical_result_id) AS merged_candidates,
    count(f.canonical_result_id) AS final_candidates,
    count(j.canonical_result_id) AS relevance_judged_candidates
FROM discovered d
JOIN multiplicity m USING (run_key, canonical_result_id)
LEFT JOIN eligible e USING (run_key, provider, canonical_result_id)
LEFT JOIN search_candidates c USING (run_key, canonical_result_id)
LEFT JOIN final_results f USING (run_key, canonical_result_id)
LEFT JOIN judged j USING (run_key, canonical_result_id)
GROUP BY d.run_key, d.provider;

CREATE VIEW vw_branch_contribution AS
WITH discovered AS (
    SELECT DISTINCT run_key, branch_id, canonical_result_id
    FROM provider_results
), multiplicity AS (
    SELECT run_key, canonical_result_id, count(*) AS branch_count
    FROM discovered
    GROUP BY ALL
), eligible AS (
    SELECT DISTINCT run_key, branch_id, canonical_result_id
    FROM provider_results
    WHERE is_eligible
)
SELECT
    d.run_key,
    d.branch_id,
    count(*) AS discovered_unique,
    count(*) FILTER (WHERE m.branch_count = 1) AS exclusive_candidates,
    sum(1.0 / m.branch_count) AS fractional_discovery_credit,
    count(e.canonical_result_id) AS eligible_candidates,
    count(c.canonical_result_id) AS merged_candidates,
    count(f.canonical_result_id) AS final_candidates
FROM discovered d
JOIN multiplicity m USING (run_key, canonical_result_id)
LEFT JOIN eligible e USING (run_key, branch_id, canonical_result_id)
LEFT JOIN search_candidates c USING (run_key, canonical_result_id)
LEFT JOIN final_results f USING (run_key, canonical_result_id)
GROUP BY d.run_key, d.branch_id;

CREATE VIEW vw_rewrite_value AS
SELECT
    v.run_key,
    v.variant_id,
    v.variant_role,
    v.query_text,
    v.proposed,
    v.selected,
    v.executed,
    count(b.branch_id) AS branches,
    coalesce(sum(bc.discovered_unique), 0) AS discovered_unique,
    coalesce(sum(bc.exclusive_candidates), 0) AS exclusive_candidates,
    coalesce(sum(bc.fractional_discovery_credit), 0) AS fractional_discovery_credit,
    coalesce(sum(bc.merged_candidates), 0) AS merged_candidates,
    coalesce(sum(bc.final_candidates), 0) AS final_candidates
FROM query_variants v
LEFT JOIN search_branches b USING (variant_id, run_key)
LEFT JOIN vw_branch_contribution bc USING (branch_id, run_key)
GROUP BY ALL;

CREATE VIEW vw_followup_attribution AS
WITH explicit_edges AS (
    SELECT
        f.fetch_id,
        f.fetch_tool_call_id,
        f.canonical_result_id,
        o.output_item_id AS source_output_item_id,
        o.tool_call_id AS source_tool_call_id,
        o.run_key AS source_run_key,
        'explicit' AS attribution_method,
        1.0::DOUBLE AS attribution_confidence,
        date_diff('millisecond', o.recorded_at, f.started_at) AS lag_ms
    FROM content_fetches f
    JOIN tool_output_items o ON o.output_item_id = f.source_output_item_id
), inferred_candidates AS (
    SELECT
        f.fetch_id,
        f.fetch_tool_call_id,
        f.canonical_result_id,
        o.output_item_id AS source_output_item_id,
        o.tool_call_id AS source_tool_call_id,
        o.run_key AS source_run_key,
        'inferred_same_session_result_30m' AS attribution_method,
        0.65::DOUBLE AS attribution_confidence,
        date_diff('millisecond', o.recorded_at, f.started_at) AS lag_ms,
        row_number() OVER (
            PARTITION BY f.fetch_id
            ORDER BY o.recorded_at DESC, o.output_item_id
        ) AS candidate_order
    FROM content_fetches f
    JOIN tool_output_items o
      ON f.source_output_item_id IS NULL
     AND o.session_id = f.session_id
     AND o.canonical_result_id = f.canonical_result_id
     AND o.recorded_at <= f.started_at
     AND f.started_at <= o.recorded_at + INTERVAL 30 MINUTE
), inferred_edges AS (
    SELECT * EXCLUDE (candidate_order)
    FROM inferred_candidates
    WHERE candidate_order = 1
)
SELECT * FROM explicit_edges
UNION ALL
SELECT * FROM inferred_edges;

CREATE VIEW vw_result_usefulness AS
WITH relevance AS (
    SELECT
        tool_call_id,
        canonical_result_id,
        avg(score) FILTER (WHERE facet_name = 'relevance') AS relevance_score,
        count(*) FILTER (WHERE facet_name = 'relevance') AS relevance_judgments,
        avg(score) FILTER (WHERE facet_name = 'factuality') AS factuality_score
    FROM judgment_facets
    GROUP BY tool_call_id, canonical_result_id
), fetch_rollup AS (
    SELECT
        a.source_output_item_id AS output_item_id,
        count(*) AS fetch_attempts,
        count(*) FILTER (WHERE f.status = 'success') AS successful_fetches,
        max(f.content_chars) AS max_content_chars,
        string_agg(DISTINCT a.attribution_method, ',') AS attribution_methods
    FROM vw_followup_attribution a
    JOIN content_fetches f USING (fetch_id)
    GROUP BY a.source_output_item_id
)
SELECT
    o.output_item_id,
    o.tool_call_id,
    o.run_key,
    o.item_type,
    o.item_rank,
    o.canonical_result_id,
    rc.canonical_url,
    coalesce(fr.fetch_attempts, 0) AS fetch_attempts,
    coalesce(fr.successful_fetches, 0) AS successful_fetches,
    fr.max_content_chars,
    fr.attribution_methods,
    r.relevance_score,
    coalesce(r.relevance_judgments, 0) AS relevance_judgments,
    r.factuality_score
FROM tool_output_items o
LEFT JOIN result_catalog rc USING (canonical_result_id)
LEFT JOIN fetch_rollup fr USING (output_item_id)
LEFT JOIN relevance r USING (tool_call_id, canonical_result_id);

CREATE VIEW vw_judgment_coverage AS
WITH relevance AS (
    SELECT
        tool_call_id,
        canonical_result_id,
        avg(score) AS relevance_score
    FROM judgment_facets
    WHERE facet_name = 'relevance'
    GROUP BY tool_call_id, canonical_result_id
)
SELECT
    coalesce(o.run_key, 'tool:' || o.tool_call_id) AS attribution_key,
    o.run_key,
    o.tool_call_id,
    count(*) AS output_items,
    count(r.canonical_result_id) AS relevance_judged_items,
    count(r.canonical_result_id)::DOUBLE / nullif(count(*), 0) AS judgment_coverage,
    avg(r.relevance_score) AS avg_judged_relevance
FROM tool_output_items o
LEFT JOIN relevance r USING (tool_call_id, canonical_result_id)
GROUP BY ALL;

CREATE VIEW vw_cost_attribution AS
SELECT
    coalesce(run_key, 'tool:' || tool_call_id) AS attribution_key,
    run_key,
    tool_call_id,
    purpose,
    provider,
    model_id,
    count(*) AS calls,
    sum(prompt_tokens) AS prompt_tokens,
    sum(completion_tokens) AS completion_tokens,
    sum(cost_usd) AS cost_usd,
    sum(latency_ms) AS latency_ms
FROM llm_calls
GROUP BY ALL;

CREATE VIEW vw_embedding_coverage AS
SELECT
    'query' AS subject_type,
    model_id,
    count(*) AS embeddings,
    count(DISTINCT run_key) AS runs,
    count(DISTINCT embedded_text_hash) AS text_versions
FROM query_embeddings
GROUP BY model_id
UNION ALL
SELECT
    'candidate',
    model_id,
    count(*),
    count(DISTINCT run_key),
    count(DISTINCT embedded_text_hash)
FROM candidate_embeddings
GROUP BY model_id;

CREATE VIEW vw_dense_score_calibration AS
SELECT
    e.run_key,
    e.stage_execution_id,
    floor(e.score_after * 10) / 10 AS score_bin,
    count(*) AS candidates,
    avg(e.survived::INTEGER) AS survival_rate,
    avg((f.canonical_result_id IS NOT NULL)::INTEGER) AS final_rate,
    avg(j.score) FILTER (WHERE j.facet_name = 'relevance') AS avg_relevance,
    count(j.judgment_id) FILTER (WHERE j.facet_name = 'relevance') AS judgment_coverage_count
FROM candidate_stage_events e
LEFT JOIN final_results f USING (run_key, canonical_result_id)
LEFT JOIN judgment_facets j USING (run_key, canonical_result_id)
WHERE e.score_after IS NOT NULL
GROUP BY e.run_key, e.stage_execution_id, score_bin;

CREATE VIEW vw_data_quality_issues AS
SELECT
    'tool_lifecycle_' || lifecycle_status AS issue_type,
    tool_call_id AS entity_id,
    'requests=' || request_events || ', terminals=' || terminal_events AS detail
FROM vw_tool_invocations
WHERE lifecycle_status NOT IN ('success', 'error')
UNION ALL
SELECT
    'stage_output_count_mismatch',
    s.stage_execution_id,
    'recorded=' || s.output_count || ', observed=' || coalesce(x.observed, 0)
FROM rerank_stage_executions s
LEFT JOIN (
    SELECT stage_execution_id, count(*) FILTER (WHERE survived) AS observed
    FROM candidate_stage_events
    GROUP BY stage_execution_id
) x USING (stage_execution_id)
WHERE s.status != 'skipped'
  AND s.output_count != coalesce(x.observed, 0)
UNION ALL
SELECT
    'missing_explicit_output_item',
    f.fetch_id,
    'source_output_item_id=' || f.source_output_item_id
FROM content_fetches f
LEFT JOIN tool_output_items o ON o.output_item_id = f.source_output_item_id
WHERE f.source_output_item_id IS NOT NULL
  AND o.output_item_id IS NULL;

CREATE MACRO semantic_query_neighbors(query_vec, neighbor_count := 5) AS TABLE
SELECT
    run_key,
    model_id,
    array_cosine_distance(embedding, query_vec::FLOAT[3]) AS cosine_distance
FROM query_embeddings
ORDER BY cosine_distance
LIMIT neighbor_count;
"""


class SchemaState:
    def __init__(self) -> None:
        self.conn = duckdb.connect(":memory:")
        self.conn.execute(SCHEMA_SQL)
        self.view: ViewName = "funnel"
        self.note = "reset: empty in-memory DuckDB schema"
        self.last_action = "0"
        self.clock_ms = 0

    def now(self) -> datetime:
        self.clock_ms += 1_000
        return BASE_TIME + timedelta(milliseconds=self.clock_ms)

    def close(self) -> None:
        self.conn.close()


def canonical_id(url: str) -> str:
    return f"cid:{canonicalize(url)}"


def _rows(state: SchemaState, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = state.conn.execute(sql, params or [])
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

def _one(state: SchemaState, sql: str, params: list[Any] | None = None) -> tuple[Any, ...]:
    row = state.conn.execute(sql, params or []).fetchone()
    if row is None:
        raise RuntimeError(f"prototype query returned no row: {sql}")
    return row


def _insert(
    state: SchemaState,
    table: str,
    columns: tuple[str, ...],
    values: list[tuple[Any, ...]],
) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _ in columns)
    state.conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def _exists(state: SchemaState, table: str, where: str, params: list[Any]) -> bool:
    return bool(_one(state, f"SELECT count(*) FROM {table} WHERE {where}", params)[0])


def _result(url: str, when: datetime) -> tuple[str, str, str, datetime]:
    canonical_url = canonicalize(url)
    domain = canonical_url.split("/", 3)[2]
    return canonical_id(url), canonical_url, domain, when


def reduce(state: SchemaState, action: str) -> SchemaState:
    actions = {
        "1": _seed_web_search,
        "2": _skip_bi_encoder,
        "3": _cross_encoder,
        "4": _rankllm_fail_open,
        "5": _finalize,
        "6": _quick_search,
        "7": _content_fetches,
        "8": _judgments,
        "9": _lifecycle_anomalies,
        "x": _drop_all_scenario,
        "v": _cycle_view,
    }
    if action == "0":
        state.close()
        return SchemaState()
    fn = actions.get(action)
    if fn is None:
        state.note = f"unknown action {action!r}"
        return state
    state.last_action = action
    return fn(state)


def _cycle_view(state: SchemaState) -> SchemaState:
    state.view = VIEWS[(VIEWS.index(state.view) + 1) % len(VIEWS)]
    state.note = f"view -> {state.view}"
    return state


def _seed_web_search(state: SchemaState) -> SchemaState:
    if _exists(state, "search_runs", "run_key = ?", ["run-web-1"]):
        state.note = "run-web-1 already seeded"
        return state

    started = state.now()
    a = "https://alpha.example/doc?a=1&utm_source=feed"
    b = "https://beta.example/guide"
    c = "https://gamma.example/report"
    d = "https://blocked.example/spam"
    ca, cb, cc, cd = (canonical_id(url) for url in (a, b, c, d))

    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "recorded_at"),
        [("ev-web-request", "tool-web-1", "session-1", "run-web-1", "web_search", "request", started)],
    )
    _insert(
        state,
        "search_runs",
        (
            "run_key",
            "tool_call_id",
            "session_id",
            "original_query",
            "normalized_query",
            "intent",
            "requested_results",
            "status",
            "started_at",
        ),
        [("run-web-1", "tool-web-1", "session-1", "duckdb vector analytics", "duckdb vector analytics", "technical", 5, "running", started)],
    )
    _insert(
        state,
        "query_variants",
        (
            "variant_id",
            "run_key",
            "parent_variant_id",
            "variant_order",
            "variant_role",
            "query_text",
            "proposed",
            "selected",
            "executed",
        ),
        [
            ("variant-original", "run-web-1", None, 0, "original", "duckdb vector analytics", True, True, True),
            ("variant-rewrite", "run-web-1", "variant-original", 1, "rewrite", "DuckDB VSS search analytics", True, True, True),
        ],
    )
    _insert(
        state,
        "search_branches",
        ("branch_id", "run_key", "variant_id", "branch_order", "branch_role", "status", "started_at", "completed_at"),
        [
            ("branch-original", "run-web-1", "variant-original", 0, "baseline", "success", started, state.now()),
            ("branch-rewrite", "run-web-1", "variant-rewrite", 1, "rewrite", "success", started, state.now()),
        ],
    )
    calls = [
        ("pc-brave-original", "branch-original", "brave", "duckdb vector analytics", 26.0),
        ("pc-brave-rewrite", "branch-rewrite", "brave", "DuckDB VSS search analytics", 31.0),
        ("pc-tavily-rewrite", "branch-rewrite", "tavily", "DuckDB VSS search analytics", 44.0),
    ]
    _insert(
        state,
        "provider_calls",
        (
            "provider_call_id",
            "run_key",
            "branch_id",
            "provider",
            "request_query",
            "requested_results",
            "status",
            "latency_ms",
            "started_at",
            "completed_at",
        ),
        [
            (call_id, "run-web-1", branch, provider, query, 5, "success", latency, started, state.now())
            for call_id, branch, provider, query, latency in calls
        ],
    )
    _insert(
        state,
        "result_catalog",
        ("canonical_result_id", "canonical_url", "domain", "first_seen_at"),
        [_result(url, state.now()) for url in (a, b, c, d)],
    )
    provider_rows = [
        ("pr-1", "pc-brave-original", "branch-original", "brave", 1, ca, a, "Alpha", 0.91, True, None),
        ("pr-2", "pc-brave-original", "branch-original", "brave", 2, cb, b, "Beta", 0.74, True, None),
        ("pr-3", "pc-brave-rewrite", "branch-rewrite", "brave", 1, cb, b, "Beta", 0.88, True, None),
        ("pr-4", "pc-brave-rewrite", "branch-rewrite", "brave", 2, cc, c, "Gamma", 0.72, True, None),
        ("pr-5", "pc-tavily-rewrite", "branch-rewrite", "tavily", 1, ca, a, "Alpha", 0.86, True, None),
        ("pr-6", "pc-tavily-rewrite", "branch-rewrite", "tavily", 2, cd, d, "Blocked", 0.84, False, "blocked_domain"),
    ]
    _insert(
        state,
        "provider_results",
        (
            "provider_result_id",
            "provider_call_id",
            "run_key",
            "branch_id",
            "provider",
            "provider_rank",
            "canonical_result_id",
            "raw_url",
            "title",
            "raw_score",
            "is_eligible",
            "rejection_reason",
            "recorded_at",
        ),
        [row[:2] + ("run-web-1",) + row[2:] + (state.now(),) for row in provider_rows],
    )
    _insert(
        state,
        "search_candidates",
        ("run_key", "canonical_result_id", "merge_rank", "rrf_score", "bm25_score", "recorded_at"),
        [
            ("run-web-1", ca, 1, 0.0318, 4.1, state.now()),
            ("run-web-1", cb, 2, 0.0315, 3.8, state.now()),
            ("run-web-1", cc, 3, 0.0161, 3.2, state.now()),
        ],
    )
    _insert(
        state,
        "embedding_models",
        ("model_id", "dimensions", "distance_metric", "normalized", "text_recipe_version"),
        [("prototype-e5-3d", 3, "cosine", True, "query-or-title-v1")],
    )
    _insert(
        state,
        "query_embeddings",
        ("run_key", "model_id", "embedded_text_hash", "embedding", "recorded_at"),
        [("run-web-1", "prototype-e5-3d", "hash-query-1", [0.95, 0.05, 0.0], state.now())],
    )
    _insert(
        state,
        "candidate_embeddings",
        ("run_key", "canonical_result_id", "model_id", "embedded_text_hash", "embedding", "recorded_at"),
        [
            ("run-web-1", ca, "prototype-e5-3d", "hash-a", [0.94, 0.06, 0.0], state.now()),
            ("run-web-1", cb, "prototype-e5-3d", "hash-b", [0.76, 0.24, 0.0], state.now()),
            ("run-web-1", cc, "prototype-e5-3d", "hash-c", [0.51, 0.49, 0.0], state.now()),
        ],
    )
    state.note = "seeded normalized retrieval lineage: 2 variants, 2 branches, 3 calls, 6 provider rows"
    return state


def _current_survivors(state: SchemaState, run_key: str) -> list[tuple[str, int]]:
    latest = state.conn.execute(
        "SELECT stage_execution_id, status FROM rerank_stage_executions "
        "WHERE run_key = ? ORDER BY stage_order DESC, attempt DESC LIMIT 1",
        [run_key],
    ).fetchone()
    if latest is None:
        return state.conn.execute(
            "SELECT canonical_result_id, merge_rank FROM search_candidates WHERE run_key = ? ORDER BY merge_rank",
            [run_key],
        ).fetchall()
    stage_id, status = latest
    if status == "skipped":
        previous = state.conn.execute(
            "SELECT stage_execution_id FROM rerank_stage_executions "
            "WHERE run_key = ? AND stage_execution_id != ? AND status != 'skipped' "
            "ORDER BY stage_order DESC, attempt DESC LIMIT 1",
            [run_key, stage_id],
        ).fetchone()
        if previous is None:
            return state.conn.execute(
                "SELECT canonical_result_id, merge_rank FROM search_candidates WHERE run_key = ? ORDER BY merge_rank",
                [run_key],
            ).fetchall()
        stage_id = previous[0]
    return state.conn.execute(
        "SELECT canonical_result_id, rank_after FROM candidate_stage_events "
        "WHERE stage_execution_id = ? AND survived ORDER BY rank_after",
        [stage_id],
    ).fetchall()


def _require_stage(state: SchemaState, required: str | None) -> bool:
    if not _exists(state, "search_runs", "run_key = ?", ["run-web-1"]):
        state.note = "seed run first"
        return False
    latest = state.conn.execute(
        "SELECT stage_name FROM rerank_stage_executions WHERE run_key = 'run-web-1' "
        "ORDER BY stage_order DESC LIMIT 1"
    ).fetchone()
    actual = latest[0] if latest else None
    if actual != required:
        state.note = f"stage order rejected: expected prior={required!r}, actual={actual!r}"
        return False
    return True


def _skip_bi_encoder(state: SchemaState) -> SchemaState:
    if not _require_stage(state, None):
        return state
    survivors = _current_survivors(state, "run-web-1")
    _insert(
        state,
        "rerank_stage_executions",
        (
            "stage_execution_id",
            "run_key",
            "stage_order",
            "attempt",
            "stage_name",
            "status",
            "fail_open",
            "input_count",
            "output_count",
            "recorded_at",
        ),
        [("stage-bi-1", "run-web-1", 10, 1, "bi_encoder", "skipped", False, len(survivors), None, state.now())],
    )
    state.note = "bi_encoder skipped: NULL output and no candidate rows, not zero survivors"
    return state


def _cross_encoder(state: SchemaState) -> SchemaState:
    if not _require_stage(state, "bi_encoder"):
        return state
    survivors = _current_survivors(state, "run-web-1")
    output = survivors[:1]
    _insert(
        state,
        "rerank_stage_executions",
        (
            "stage_execution_id",
            "run_key",
            "stage_order",
            "attempt",
            "stage_name",
            "status",
            "fail_open",
            "input_count",
            "output_count",
            "model_id",
            "threshold",
            "duration_ms",
            "recorded_at",
        ),
        [("stage-cross-1", "run-web-1", 20, 1, "cross_encoder", "success", False, len(survivors), len(output), "cross-v1", 0.8, 18.0, state.now())],
    )
    events = []
    for rank, (canon, prior_rank) in enumerate(survivors, start=1):
        survived = rank <= len(output)
        events.append(
            (
                "stage-cross-1",
                "run-web-1",
                canon,
                True,
                survived,
                prior_rank,
                rank if survived else None,
                None,
                0.92 if survived else 0.4 - rank / 100,
                "cross_probability",
                None if survived else "below_threshold",
                state.now(),
            )
        )
    _insert(
        state,
        "candidate_stage_events",
        (
            "stage_execution_id",
            "run_key",
            "canonical_result_id",
            "entered",
            "survived",
            "rank_before",
            "rank_after",
            "score_before",
            "score_after",
            "score_name",
            "removal_reason",
            "recorded_at",
        ),
        events,
    )
    state.note = "cross_encoder ran: one survivor; removals and rank movement are candidate facts"
    return state


def _rankllm_fail_open(state: SchemaState) -> SchemaState:
    if not _require_stage(state, "cross_encoder"):
        return state
    survivors = _current_survivors(state, "run-web-1")
    _insert(
        state,
        "rerank_stage_executions",
        (
            "stage_execution_id",
            "run_key",
            "stage_order",
            "attempt",
            "stage_name",
            "status",
            "fail_open",
            "input_count",
            "output_count",
            "model_id",
            "duration_ms",
            "error_type",
            "recorded_at",
        ),
        [("stage-rankllm-1", "run-web-1", 30, 1, "rankllm", "error", True, len(survivors), len(survivors), "rankllm-v1", 23.0, "timeout", state.now())],
    )
    _insert(
        state,
        "candidate_stage_events",
        (
            "stage_execution_id",
            "run_key",
            "canonical_result_id",
            "entered",
            "survived",
            "rank_before",
            "rank_after",
            "score_name",
            "removal_reason",
            "recorded_at",
        ),
        [
            ("stage-rankllm-1", "run-web-1", canon, True, True, rank, rank, "fail_open_prior_rank", None, state.now())
            for canon, rank in survivors
        ],
    )
    state.note = "rankllm failed open from the immediately prior survivors; removed candidates cannot resurrect"
    return state


def _finalize(state: SchemaState) -> SchemaState:
    if not _require_stage(state, "rankllm"):
        return state
    if _exists(state, "final_results", "run_key = ?", ["run-web-1"]):
        state.note = "run-web-1 already finalized"
        return state
    survivors = _current_survivors(state, "run-web-1")
    now = state.now()
    _insert(
        state,
        "final_results",
        ("run_key", "final_rank", "canonical_result_id", "title", "final_score", "recorded_at"),
        [("run-web-1", rank, canon, "Alpha", 0.92, now) for canon, rank in survivors],
    )
    _insert(
        state,
        "tool_output_items",
        (
            "output_item_id",
            "tool_call_id",
            "session_id",
            "run_key",
            "item_type",
            "item_rank",
            "canonical_result_id",
            "raw_url",
            "title",
            "recorded_at",
        ),
        [
            (
                f"web-item-{rank}",
                "tool-web-1",
                "session-1",
                "run-web-1",
                "search_result",
                rank,
                canon,
                _one(state, "SELECT canonical_url FROM result_catalog WHERE canonical_result_id = ?", [canon])[0],
                "Alpha",
                now,
            )
            for canon, rank in survivors
        ],
    )
    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "recorded_at"),
        [("ev-web-response", "tool-web-1", "session-1", "run-web-1", "web_search", "response", now)],
    )
    state.conn.execute(
        "UPDATE search_runs SET status = 'success', completed_at = ? WHERE run_key = 'run-web-1'",
        [now],
    )
    state.note = f"finalized {len(survivors)} result(s) and emitted output-item identities"
    return state


def _quick_search(state: SchemaState) -> SchemaState:
    if _exists(state, "tool_events", "tool_call_id = ?", ["tool-quick-1"]):
        state.note = "quick tool path already seeded"
        return state
    start = state.now()
    c = "https://gamma.example/report?utm_campaign=x"
    e = "https://epsilon.example/news"
    cc, ce = canonical_id(c), canonical_id(e)
    if not _exists(state, "result_catalog", "canonical_result_id = ?", [cc]):
        _insert(state, "result_catalog", ("canonical_result_id", "canonical_url", "domain", "first_seen_at"), [_result(c, start)])
    if not _exists(state, "result_catalog", "canonical_result_id = ?", [ce]):
        _insert(state, "result_catalog", ("canonical_result_id", "canonical_url", "domain", "first_seen_at"), [_result(e, start)])
    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "recorded_at"),
        [
            ("ev-quick-request", "tool-quick-1", "session-1", None, "quick_web_search", "request", start),
            ("ev-quick-response", "tool-quick-1", "session-1", None, "quick_web_search", "response", state.now()),
        ],
    )
    _insert(
        state,
        "tool_output_items",
        (
            "output_item_id",
            "tool_call_id",
            "session_id",
            "run_key",
            "item_type",
            "item_rank",
            "canonical_result_id",
            "raw_url",
            "title",
            "recorded_at",
        ),
        [
            ("quick-item-1", "tool-quick-1", "session-1", None, "search_result", 1, cc, c, "Gamma", state.now()),
            ("quick-item-2", "tool-quick-1", "session-1", None, "search_result", 2, ce, e, "Epsilon", state.now()),
            ("other-session-gamma", "tool-other", "session-other", None, "search_result", 1, cc, c, "Gamma other", state.now()),
        ],
    )
    state.note = "quick_web_search recorded as a first-class tool path without inventing a search run"
    return state


def _content_fetches(state: SchemaState) -> SchemaState:
    if not _exists(state, "tool_output_items", "output_item_id = ?", ["quick-item-1"]):
        state.note = "seed quick tool first"
        return state
    if _exists(state, "content_fetches", "fetch_tool_call_id = ?", ["tool-fetch-1"]):
        state.note = "content fetches already seeded"
        return state
    start = state.now()
    ca = canonical_id("https://alpha.example/doc?a=1")
    cc = canonical_id("https://gamma.example/report")
    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "recorded_at"),
        [
            ("ev-fetch-request", "tool-fetch-1", "session-1", None, "batch_get_content", "request", start),
            ("ev-fetch-response", "tool-fetch-1", "session-1", None, "batch_get_content", "response", state.now()),
        ],
    )
    _insert(
        state,
        "content_fetches",
        (
            "fetch_id",
            "fetch_tool_call_id",
            "session_id",
            "run_key",
            "source_output_item_id",
            "canonical_result_id",
            "input_url",
            "status",
            "content_chars",
            "fetch_backend",
            "cache_status",
            "started_at",
            "completed_at",
        ),
        [
            ("fetch-explicit", "tool-fetch-1", "session-1", "run-web-1", "web-item-1", ca, "https://alpha.example/doc?a=1", "success", 12_000, "jina", "miss", start, state.now()),
            ("fetch-inferred", "tool-fetch-1", "session-1", None, None, cc, "https://gamma.example/report", "success", 8_000, "defuddle", "hit", state.now(), state.now()),
        ],
    )
    state.note = "fetch lineage: one explicit output-item edge and one bounded same-session inference"
    return state


def _judgments(state: SchemaState) -> SchemaState:
    if _exists(state, "judgment_facets", "judgment_id = ?", ["judge-1"]):
        state.note = "judgments already seeded"
        return state
    ca = canonical_id("https://alpha.example/doc?a=1")
    cc = canonical_id("https://gamma.example/report")
    _insert(
        state,
        "judgment_facets",
        (
            "judgment_id",
            "run_key",
            "tool_call_id",
            "canonical_result_id",
            "facet_name",
            "score",
            "judge_model_id",
            "rubric_version",
            "reasoning",
            "recorded_at",
        ),
        [
            ("judge-1", "run-web-1", "tool-web-1", ca, "relevance", 0.95, "judge-v1", "search-result-v2", "directly relevant", state.now()),
            ("judge-2", "run-web-1", "tool-web-1", ca, "factuality", 0.88, "judge-v1", "search-result-v2", "claims supported", state.now()),
            ("judge-3", None, "tool-quick-1", cc, "relevance", 0.72, "judge-v1", "search-result-v2", "partially relevant", state.now()),
        ],
    )
    _insert(
        state,
        "llm_calls",
        (
            "llm_call_id",
            "run_key",
            "tool_call_id",
            "purpose",
            "provider",
            "model_id",
            "status",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
            "latency_ms",
            "recorded_at",
        ),
        [
            ("llm-rewrite-1", "run-web-1", "tool-web-1", "query_rewrite", "openrouter", "model-a", "success", 500, 80, 0.0012, 220.0, state.now()),
            ("llm-judge-1", None, "tool-quick-1", "result_judgment", "gemini", "judge-v1", "success", 700, 120, 0.0021, 310.0, state.now()),
        ],
    )
    state.note = "facet facts added; quality metrics remain separate and expose judgment coverage"
    return state


def _lifecycle_anomalies(state: SchemaState) -> SchemaState:
    if _exists(state, "tool_events", "tool_call_id = ?", ["tool-incomplete"]):
        state.note = "lifecycle anomalies already seeded"
        return state
    now = state.now()
    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "error_type", "recorded_at"),
        [
            ("ev-incomplete-request", "tool-incomplete", "session-1", None, "get_content", "request", None, now),
            ("ev-orphan-response", "tool-orphan", "session-1", None, "gemini_search", "response", None, state.now()),
            ("ev-conflict-request", "tool-conflict", "session-1", None, "web_search", "request", None, state.now()),
            ("ev-conflict-response", "tool-conflict", "session-1", None, "web_search", "response", None, state.now()),
            ("ev-conflict-error", "tool-conflict", "session-1", None, "web_search", "error", "late_error", state.now()),
        ],
    )
    state.note = "request-only, orphan-terminal, and conflicting-terminal lifecycles stay visible as quality issues"
    return state


def _drop_all_scenario(state: SchemaState) -> SchemaState:
    if _exists(state, "search_runs", "run_key = ?", ["run-drop-all"]):
        state.note = "drop-all scenario already seeded"
        return state
    start = state.now()
    urls = ("https://zeta.example/one", "https://eta.example/two")
    cids = tuple(canonical_id(url) for url in urls)
    _insert(
        state,
        "search_runs",
        ("run_key", "tool_call_id", "session_id", "original_query", "normalized_query", "intent", "requested_results", "status", "started_at", "completed_at"),
        [("run-drop-all", "tool-drop-all", "session-2", "adversarial", "adversarial", "technical", 2, "success", start, state.now())],
    )
    _insert(
        state,
        "query_variants",
        ("variant_id", "run_key", "variant_order", "variant_role", "query_text", "proposed", "selected", "executed"),
        [("variant-drop-all", "run-drop-all", 0, "original", "adversarial", True, True, True)],
    )
    _insert(
        state,
        "search_branches",
        ("branch_id", "run_key", "variant_id", "branch_order", "branch_role", "status", "started_at", "completed_at"),
        [("branch-drop-all", "run-drop-all", "variant-drop-all", 0, "baseline", "success", start, state.now())],
    )
    _insert(
        state,
        "provider_calls",
        ("provider_call_id", "run_key", "branch_id", "provider", "request_query", "requested_results", "status", "latency_ms", "started_at", "completed_at"),
        [("pc-drop-all", "run-drop-all", "branch-drop-all", "brave", "adversarial", 2, "success", 10.0, start, state.now())],
    )
    _insert(state, "result_catalog", ("canonical_result_id", "canonical_url", "domain", "first_seen_at"), [_result(url, state.now()) for url in urls])
    _insert(
        state,
        "provider_results",
        ("provider_result_id", "provider_call_id", "run_key", "branch_id", "provider", "provider_rank", "canonical_result_id", "raw_url", "is_eligible", "recorded_at"),
        [
            (f"pr-drop-{rank}", "pc-drop-all", "run-drop-all", "branch-drop-all", "brave", rank, cid, url, True, state.now())
            for rank, (cid, url) in enumerate(zip(cids, urls, strict=True), start=1)
        ],
    )
    _insert(
        state,
        "search_candidates",
        ("run_key", "canonical_result_id", "merge_rank", "rrf_score", "recorded_at"),
        [("run-drop-all", cid, rank, 0.01, state.now()) for rank, cid in enumerate(cids, start=1)],
    )
    _insert(
        state,
        "rerank_stage_executions",
        ("stage_execution_id", "run_key", "stage_order", "attempt", "stage_name", "status", "fail_open", "input_count", "output_count", "recorded_at"),
        [
            ("stage-drop-cross", "run-drop-all", 20, 1, "cross_encoder", "success", False, 2, 0, state.now()),
            ("stage-drop-rankllm", "run-drop-all", 30, 1, "rankllm", "error", True, 0, 0, state.now()),
        ],
    )
    _insert(
        state,
        "candidate_stage_events",
        ("stage_execution_id", "run_key", "canonical_result_id", "entered", "survived", "rank_before", "score_after", "score_name", "removal_reason", "recorded_at"),
        [
            ("stage-drop-cross", "run-drop-all", cid, True, False, rank, 0.1, "cross_probability", "below_threshold", state.now())
            for rank, cid in enumerate(cids, start=1)
        ],
    )
    _insert(
        state,
        "tool_events",
        ("event_id", "tool_call_id", "session_id", "run_key", "tool_name", "event_type", "recorded_at"),
        [
            ("ev-drop-request", "tool-drop-all", "session-2", "run-drop-all", "web_search", "request", start),
            ("ev-drop-response", "tool-drop-all", "session-2", "run-drop-all", "web_search", "response", state.now()),
        ],
    )
    state.note = "adversarial run: cross output=0 and fail-open consumes 0, proving no candidate resurrection"
    return state


def view_rows(state: SchemaState, limit: int = 30) -> list[dict[str, Any]]:
    views = {
        "invocations": "SELECT * FROM vw_tool_invocations ORDER BY started_at NULLS LAST, tool_call_id",
        "funnel": "SELECT * FROM vw_run_stage_funnel ORDER BY run_key, stage_order",
        "trajectory": "SELECT * FROM vw_candidate_trajectory ORDER BY run_key, canonical_result_id, stage_order",
        "branch": "SELECT * FROM vw_branch_contribution ORDER BY run_key, branch_id",
        "provider": "SELECT * FROM vw_provider_contribution ORDER BY run_key, provider",
        "rewrite": "SELECT * FROM vw_rewrite_value ORDER BY run_key, variant_id",
        "usefulness": "SELECT * FROM vw_result_usefulness ORDER BY tool_call_id, item_rank",
        "judgments": "SELECT * FROM vw_judgment_coverage ORDER BY attribution_key",
        "followups": "SELECT * FROM vw_followup_attribution ORDER BY fetch_id",
        "quality": "SELECT * FROM vw_data_quality_issues ORDER BY issue_type, entity_id",
        "embeddings": "SELECT * FROM vw_embedding_coverage ORDER BY subject_type, model_id",
        "cost": "SELECT * FROM vw_cost_attribution ORDER BY attribution_key, purpose",
    }
    rows = _rows(state, f"{views[state.view]} LIMIT {int(limit)}")
    if state.view == "embeddings" and _exists(state, "query_embeddings", "run_key = ?", ["run-web-1"]):
        query_vec = _one(state, "SELECT embedding FROM query_embeddings WHERE run_key = 'run-web-1'")[0]
        neighbors = _rows(state, "SELECT * FROM semantic_query_neighbors(?, 5)", [query_vec])
        rows.extend({"subject_type": "nearest_query", **row} for row in neighbors)
        rows.append({"subject_type": "vss_decision", **VSS_RECOMMENDATION})
    return rows


def counts(state: SchemaState) -> dict[str, int]:
    return {
        table: _one(state, f"SELECT count(*) FROM {table}")[0]
        for table in TABLES
    }


def integrity_summary(state: SchemaState) -> dict[str, Any]:
    return {
        "issues": _one(state, "SELECT count(*) FROM vw_data_quality_issues")[0],
        "tool_invocations": _one(state, "SELECT count(*) FROM vw_tool_invocations")[0],
        "runs": _one(state, "SELECT count(*) FROM search_runs")[0],
        "final_results": _one(state, "SELECT count(*) FROM final_results")[0],
    }
