"""Sync local DuckDB analytics into MotherDuck for Grafana querying."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ..settings import settings
from .duckdb_store import ensure_store_schema


DEFAULT_SCHEMA = "kindly_analytics"
DEFAULT_EXTENSION_DIR = Path(".kindly") / "duckdb_extensions"


@dataclass(frozen=True)
class SyncResult:
    source_path: str
    database: str
    schema: str
    inserted_rows: int
    source_rows: int


def _quote_ident(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("Identifier must be non-empty and cannot contain NUL bytes.")
    return '"' + value.replace('"', '""') + '"'


def _attach_name(database: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in database).strip("_")
    return f"md_{normalized or 'analytics'}"


def _motherduck_database(value: str | None = None) -> str:
    database = (value or os.environ.get("KINDLY_MOTHERDUCK_DATABASE") or "").strip()
    if not database:
        raise ValueError(
            "MotherDuck database is required. Set KINDLY_MOTHERDUCK_DATABASE or pass "
            "--motherduck-database."
        )
    return database


def _load_motherduck(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("INSTALL motherduck")
    connection.execute("LOAD motherduck")


def _duckdb_config() -> dict[str, str]:
    token = os.environ.get("MOTHERDUCK_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "MOTHERDUCK_TOKEN is required to sync analytics to MotherDuck."
        )
    extension_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY", "").strip() or str(
        DEFAULT_EXTENSION_DIR
    )
    Path(extension_dir).mkdir(parents=True, exist_ok=True)
    if not os.environ.get("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"):
        try:
            import certifi
        except ModuleNotFoundError:
            pass
        else:
            os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = certifi.where()
    return {
        "extension_directory": extension_dir,
    }


def build_analytics_view_sql(target: str) -> list[str]:
    return [
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
                'tool.quick_web_search.response'
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
            json_extract_string(e.payload_json, '$.provider_name') AS provider_name,
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
        f"""
        CREATE OR REPLACE VIEW {target}.vw_merged_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            json_extract_string(e.payload_json, '$.query') AS query,
            CAST(r.key AS INTEGER) AS result_index,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.merged_results')) AS r
        WHERE e.event_name = 'search.orchestrator.response'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_rewrite_variants AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.query,
            CAST(v.key AS INTEGER) AS variant_rank,
            json_extract_string(v.value, '$.kind') AS kind,
            json_extract_string(v.value, '$.target') AS target,
            json_extract_string(v.value, '$.query') AS rewritten_query,
            json_extract_string(v.value, '$.why') AS why,
            CAST(json_extract_string(v.value, '$.weight') AS DOUBLE) AS weight
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.variants')) AS v
        WHERE e.event_name = 'query.rewrite.completed'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_search_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.event_name,
            e.query,
            CAST(r.key AS INTEGER) AS result_rank,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        WHERE e.event_name IN (
            'search.orchestrator.response',
            'search.single_query.response',
            'tool.web_search.response'
        )
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_rerank_results AS
        SELECT
            e.event_id,
            e.recorded_at,
            coalesce(e.run_key, e.trace_id, e.event_id) AS run_key,
            e.query,
            e.provider,
            e.model,
            CAST(r.key AS INTEGER) AS rerank_rank,
            json_extract_string(r.value, '$.title') AS title,
            json_extract_string(r.value, '$.link') AS url,
            json_extract_string(r.value, '$.snippet') AS snippet,
            json_extract_string(r.value, '$.domain') AS domain,
            json_extract(r.value, '$.providers') AS providers_json,
            CAST(json_extract_string(r.value, '$.provider_count') AS INTEGER) AS provider_count,
            CAST(json_extract_string(r.value, '$.score') AS DOUBLE) AS score
        FROM {target}.analytics_event_raw AS e,
             json_each(json_extract(e.payload_json, '$.results')) AS r
        WHERE json_extract(e.payload_json, '$.results') IS NOT NULL
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_fetch_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            json_extract_string(payload_json, '$.input_url') AS input_url,
            json_extract_string(payload_json, '$.normalized_url') AS normalized_url,
            json_extract_string(payload_json, '$.fetched_url') AS fetched_url,
            json_extract_string(payload_json, '$.status') AS status,
            json_extract_string(payload_json, '$.source_type') AS source_type,
            json_extract_string(payload_json, '$.fetch_backend') AS fetch_backend,
            json_extract_string(payload_json, '$.content_type') AS content_type,
            json_extract_string(payload_json, '$.page_content') AS page_content,
            json_extract(payload_json, '$.metadata') AS metadata_json,
            json_extract(payload_json, '$.links') AS links_json,
            json_extract(payload_json, '$.summary') AS summary_json,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name IN ('tool.get_content.response', 'tool.batch_get_content.response')
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_answer_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            tool_name,
            query,
            research_goal,
            model,
            json_extract_string(payload_json, '$.answer') AS answer,
            json_extract(payload_json, '$.sources') AS sources_json,
            json_extract(payload_json, '$.grounding_chunks') AS grounding_chunks_json,
            json_extract(payload_json, '$.structured_result') AS structured_result_json,
            json_extract(payload_json, '$.citations') AS citations_json,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name IN (
            'tool.gemini_search.response',
            'tool.perplexity_search.response',
            'tool.quick_web_search.response'
        )
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_candidate_survival AS
        SELECT
            run_key,
            recorded_at,
            'provider' AS stage,
            event_name,
            provider_name AS source_provider,
            query,
            url,
            title,
            snippet,
            domain,
            providers_json,
            provider_count,
            score,
            raw_score,
            source_engines_json,
            category,
            published_date,
            branch_index,
            branch_query,
            branch_weight,
            result_index
        FROM {target}.vw_provider_results
        UNION ALL
        SELECT
            run_key,
            recorded_at,
            'branch' AS stage,
            event_name,
            NULL AS source_provider,
            query,
            url,
            title,
            snippet,
            domain,
            result_providers_json AS providers_json,
            provider_count,
            score,
            raw_score,
            source_engines_json,
            category,
            published_date,
            branch_index,
            branch_query,
            branch_weight,
            result_index
        FROM {target}.vw_branch_candidates
        UNION ALL
        SELECT
            run_key,
            recorded_at,
            'merged' AS stage,
            event_name,
            NULL AS source_provider,
            query,
            url,
            title,
            snippet,
            domain,
            providers_json,
            provider_count,
            score,
            NULL AS raw_score,
            NULL AS source_engines_json,
            NULL AS category,
            NULL AS published_date,
            NULL AS branch_index,
            NULL AS branch_query,
            NULL AS branch_weight,
            result_index
        FROM {target}.vw_merged_results
        UNION ALL
        SELECT
            run_key,
            recorded_at,
            'reranked' AS stage,
            event_name,
            provider AS source_provider,
            query,
            url,
            title,
            snippet,
            domain,
            providers_json,
            provider_count,
            score,
            NULL AS raw_score,
            NULL AS source_engines_json,
            NULL AS category,
            NULL AS published_date,
            NULL AS branch_index,
            NULL AS branch_query,
            NULL AS branch_weight,
            rerank_rank AS result_index
        FROM {target}.vw_rerank_results
        UNION ALL
        SELECT
            run_key,
            recorded_at,
            'final' AS stage,
            event_name,
            NULL AS source_provider,
            query,
            url,
            title,
            snippet,
            domain,
            providers_json,
            provider_count,
            score,
            NULL AS raw_score,
            NULL AS source_engines_json,
            NULL AS category,
            NULL AS published_date,
            NULL AS branch_index,
            NULL AS branch_query,
            NULL AS branch_weight,
            result_rank AS result_index
        FROM {target}.vw_search_results
        """,
    ]


def build_summary_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE TABLE {target}.analytics_event_daily AS
        SELECT
            date_trunc('day', recorded_at) AS day,
            event_name,
            tool_name,
            phase,
            provider,
            count(*) AS event_count,
            count(DISTINCT coalesce(run_key, trace_id, event_id)) AS run_count,
            avg(duration_ms) FILTER (WHERE duration_ms IS NOT NULL) AS avg_duration_ms,
            max(duration_ms) FILTER (WHERE duration_ms IS NOT NULL) AS max_duration_ms,
            sum(output_count) FILTER (WHERE output_count IS NOT NULL) AS output_count_total
        FROM {target}.analytics_event_raw
        GROUP BY 1, 2, 3, 4, 5
        """,
    ]


def sync_once(
    *,
    source_path: str | None = None,
    motherduck_database: str | None = None,
    schema: str = DEFAULT_SCHEMA,
    limit: int | None = None,
) -> SyncResult:
    source = Path(source_path or settings.analytics_duckdb_path)
    if not source.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {source}")
    ensure_store_schema(db_path=str(source))

    database = _motherduck_database(motherduck_database)
    attach = _attach_name(database)
    target = f"{_quote_ident(attach)}.{_quote_ident(schema)}"
    remote_target = _quote_ident(schema)
    limit_sql = f"LIMIT {int(limit)}" if limit and limit > 0 else ""

    connection = duckdb.connect(str(source), config=_duckdb_config())
    try:
        _load_motherduck(connection)
        connection.execute(f"ATTACH 'md:{database}' AS {_quote_ident(attach)}")
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {target}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {target}.analytics_event_raw AS
            SELECT * FROM search_events WHERE false
            """
        )
        source_rows = connection.execute(
            "SELECT count(*) FROM search_events"
        ).fetchone()[0]
        before = connection.execute(
            f"SELECT count(*) FROM {target}.analytics_event_raw"
        ).fetchone()[0]
        connection.execute(
            f"""
            INSERT INTO {target}.analytics_event_raw BY NAME
            SELECT local.*
            FROM search_events AS local
            WHERE NOT EXISTS (
                SELECT 1
                FROM {target}.analytics_event_raw AS remote
                WHERE remote.event_id = local.event_id
            )
            ORDER BY local.recorded_at
            {limit_sql}
            """
        )
        after = connection.execute(
            f"SELECT count(*) FROM {target}.analytics_event_raw"
        ).fetchone()[0]
    finally:
        connection.close()

    remote = duckdb.connect(f"md:{database}", config=_duckdb_config())
    try:
        remote.execute(f"CREATE SCHEMA IF NOT EXISTS {remote_target}")
        for statement in [
            *build_analytics_view_sql(remote_target),
            *build_summary_sql(remote_target),
        ]:
            remote.execute(statement)
    finally:
        remote.close()

    return SyncResult(
        source_path=str(source),
        database=database,
        schema=schema,
        inserted_rows=int(after - before),
        source_rows=int(source_rows),
    )


def sync_loop(
    *,
    source_path: str | None = None,
    motherduck_database: str | None = None,
    schema: str = DEFAULT_SCHEMA,
    interval_seconds: int = 300,
) -> None:
    while True:
        sync_once(
            source_path=source_path,
            motherduck_database=motherduck_database,
            schema=schema,
        )
        time.sleep(max(1, interval_seconds))
