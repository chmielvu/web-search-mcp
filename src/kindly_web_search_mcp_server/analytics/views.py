"""Shared DuckDB/MotherDuck analytics event view SQL."""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..settings import settings
from .duckdb_store import ensure_store_schema
from .derived_views import build_derived_view_sql
from .candidate_views import build_candidate_view_sql
from .evals import build_eval_view_sql, ensure_eval_tables


def build_base_view_sql(target: str) -> list[str]:
    """Build common analytics views used by local DuckDB and MotherDuck."""
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_events AS
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
            json(payload_json) AS payload,
            payload_json
        FROM {target}.analytics_event_raw
        """
    ]


def build_analytics_view_sql(target: str) -> list[str]:
    """Build all analytics views used by local DuckDB and MotherDuck."""
    return [
        *build_base_view_sql(target),
        f"""
        CREATE OR REPLACE VIEW {target}.vw_quality_events AS
        SELECT
            event_id,
            recorded_at,
            run_key,
            event_name,
            tool_name,
            phase,
            query,
            normalized_query,
            research_goal,
            provider,
            model,
            duration_ms,
            input_count,
            output_count,
            trace_id,
            span_id,
            cache_hit,
            json_extract(payload_json, '$.final_queries') AS final_queries_json,
            json_extract(payload_json, '$.variants') AS rewrite_variants_json,
            json_extract(payload_json, '$.results') AS results_json,
            json_extract(payload_json, '$.merged_results') AS merged_results_json,
            json_extract(payload_json, '$.input_results') AS input_results_json,
            json_extract(payload_json, '$.top_results') AS top_results_json,
            json_extract(payload_json, '$.branches') AS branches_json,
            json_extract(payload_json, '$.answer') AS answer_json,
            json_extract(payload_json, '$.sources') AS sources_json,
            json_extract(payload_json, '$.grounding_chunks') AS grounding_chunks_json,
            json_extract(payload_json, '$.page_content') AS page_content_json,
            json_extract(payload_json, '$.summary') AS summary_json,
            json_extract(payload_json, '$.metadata') AS metadata_json,
            json_extract(payload_json, '$.links') AS links_json,
            payload_json
        FROM {target}.analytics_event_raw
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_run_timeline AS
        SELECT
            coalesce(run_key, trace_id, event_id) AS run_key,
            min(recorded_at) AS first_seen_at,
            max(recorded_at) AS last_seen_at,
            count(*) AS event_count,
            any_value(query) FILTER (WHERE query IS NOT NULL) AS query,
            any_value(research_goal) FILTER (WHERE research_goal IS NOT NULL) AS research_goal,
            sum(CASE WHEN event_name LIKE 'query.rewrite.%' THEN 1 ELSE 0 END) AS rewrite_events,
            sum(CASE WHEN event_name LIKE 'search.rerank.%' THEN 1 ELSE 0 END) AS rerank_events,
            sum(CASE WHEN event_name LIKE 'tool.get_content.%' THEN 1 ELSE 0 END) AS fetch_events,
            sum(CASE WHEN event_name IN (
                'tool.gemini_search.response',
                'tool.perplexity_search.response',
                'tool.quick_web_search.response',
                'tool.agentic_web_research.response',
                'agentic.research.completed'
            ) THEN 1 ELSE 0 END) AS answer_events
        FROM {target}.analytics_event_raw
        GROUP BY coalesce(run_key, trace_id, event_id)
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_provider_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            coalesce(e.provider, json_extract_string(e.payload_json, '$.provider_name')) AS provider,
            coalesce(e.provider, json_extract_string(e.payload_json, '$.provider_name')) AS provider_name,
            json_extract_string(e.payload_json, '$.query') AS query,
            NULL AS branch_index,
            NULL AS branch_query,
            NULL AS branch_weight,
            CAST(r.key AS INTEGER) AS result_index,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score,
            json_extract(r.value, '$.source_engines') AS source_engines_json,
            json_extract_string(r.value, '$.category') AS category,
            CAST(json_extract_string(r.value, '$.raw_score') AS DOUBLE) AS raw_score,
            json_extract_string(r.value, '$.published_date') AS published_date
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        WHERE e.event_name = 'provider.search.result'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_branch_candidates AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            json_extract_string(e.payload_json, '$.query') AS query,
            CAST(json_extract_string(b.value, '$.index') AS INTEGER) AS branch_index,
            json_extract_string(b.value, '$.query') AS branch_query,
            json_extract(b.value, '$.providers') AS providers_json,
            CAST(json_extract_string(b.value, '$.weight') AS DOUBLE) AS branch_weight,
            CAST(r.key AS INTEGER) AS result_index,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS result_providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score,
            json_extract(r.value, '$.source_engines') AS source_engines_json,
            json_extract_string(r.value, '$.category') AS category,
            CAST(json_extract_string(r.value, '$.raw_score') AS DOUBLE) AS raw_score,
            json_extract_string(r.value, '$.published_date') AS published_date
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.branches')) AS b,
             json_each(json_extract(b.value, '$.results')) AS r
        WHERE e.event_name = 'search.orchestrator.branches'
        """,
        *build_derived_view_sql(target),
        *build_candidate_view_sql(target),
        *build_eval_view_sql(target),
    ]


def ensure_local_views(*, db_path: str | None = None) -> None:
    """Install the shared analytics views into the local DuckDB file."""
    path = Path(db_path or settings.analytics_duckdb_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    ensure_store_schema(db_path=str(path))
    ensure_eval_tables(db_path=str(path))

    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            "CREATE OR REPLACE VIEW analytics_event_raw AS SELECT * FROM search_events"
        )
        for statement in build_analytics_view_sql("main"):
            connection.execute(statement)
    finally:
        connection.close()
