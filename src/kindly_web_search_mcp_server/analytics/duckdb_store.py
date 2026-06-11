"""DuckDB-backed append-only event store for search tuning."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

import duckdb

from ..settings import settings

_LOCK = threading.Lock()
_TABLE_NAME = "search_events"
_RUNS_TABLE_NAME = "search_runs"
_QU_TABLE_NAME = "query_understanding"
_QR_TABLE_NAME = "query_rewrites"
_PC_TABLE_NAME = "provider_calls"
_PRC_TABLE_NAME = "provider_candidates"
_MC_TABLE_NAME = "merged_candidates"
_RS_TABLE_NAME = "rerank_stages"
_RC_TABLE_NAME = "rerank_candidates"
_FR_TABLE_NAME = "final_results"
_SQS_TABLE_NAME = "search_quality_scores"
_SUM_PVD_TABLE_NAME = "summary_provider_daily"
_SUM_ID_TABLE_NAME = "summary_intent_daily"
_SUM_RD_TABLE_NAME = "summary_rerank_daily"
_SUM_QD_TABLE_NAME = "summary_quality_daily"
_JE_TABLE_NAME = "judge_evaluations"
_ABE_TABLE_NAME = "ab_experiments"
_ABS_TABLE_NAME = "ab_shadow_runs"
_ABV_TABLE_NAME = "ab_experiment_variants"
_ABA_TABLE_NAME = "ab_assignments"
_ABR_TABLE_NAME = "ab_results"

def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)

def _event_value(payload: dict[str, Any], key: str) -> str | int | float | None:
    value = payload.get(key)
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)

def _provider_value(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider")
    if value is None:
        value = payload.get("provider_name")
    return value if isinstance(value, str) else None

def _int_value(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None

def _run_key(payload: dict[str, Any]) -> str | None:
    trace_id = payload.get("trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    fingerprint = payload.get("request_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        return fingerprint
    return None

def _phase(event_name: str) -> str | None:
    parts = event_name.rsplit(".", 1)
    return parts[1] if len(parts) == 2 else None

def _duration_ms_value(payload: dict[str, Any]) -> float | None:
    value = payload.get("duration_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    duration_seconds = payload.get("duration_seconds")
    if isinstance(duration_seconds, (int, float)) and not isinstance(
        duration_seconds, bool
    ):
        return round(float(duration_seconds) * 1000.0, 3)
    return None

def _input_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        (
            "input_count",
            "input_result_count",
            "input_list_count",
            "num_results_requested",
            "num_results",
            "tool_calls_count",
        ),
    )
    return value

def _output_count_value(payload: dict[str, Any]) -> int | None:
    value = _int_value(
        payload,
        (
            "output_count",
            "result_count",
            "merged_result_count",
            "final_result_count",
            "output_result_count",
            "total_returned",
            "success_count",
            "sources_count",
        ),
    )
    return value

def _ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
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
        """
    )
    existing = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({_TABLE_NAME})").fetchall()
    }
    additions = {
        "event_id": "VARCHAR",
        "run_key": "VARCHAR",
        "tool_name": "VARCHAR",
        "phase": "VARCHAR",
        "cache_hit": "VARCHAR",
    }
    for column, column_type in additions.items():
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {_TABLE_NAME} ADD COLUMN {column} {column_type}"
            )
    connection.execute(
        f"UPDATE {_TABLE_NAME} SET event_id = uuid()::VARCHAR WHERE event_id IS NULL"
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET run_key = coalesce(trace_id, json_extract_string(payload_json, '$.request_fingerprint'))
        WHERE run_key IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET tool_name = json_extract_string(payload_json, '$.tool_name')
        WHERE tool_name IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET phase = regexp_extract(event_name, '[^.]+$', 0)
        WHERE phase IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET provider = coalesce(
            provider,
            json_extract_string(payload_json, '$.provider'),
            json_extract_string(payload_json, '$.provider_name')
        )
        WHERE provider IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET input_count = coalesce(
            input_count,
            CAST(json_extract_string(payload_json, '$.input_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.input_list_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results_requested') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.num_results') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.tool_calls_count') AS INTEGER)
        )
        WHERE input_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET output_count = coalesce(
            output_count,
            CAST(json_extract_string(payload_json, '$.output_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.merged_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.final_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.output_result_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.total_returned') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.success_count') AS INTEGER),
            CAST(json_extract_string(payload_json, '$.sources_count') AS INTEGER)
        )
        WHERE output_count IS NULL
        """
    )
    connection.execute(
        f"""
        UPDATE {_TABLE_NAME}
        SET duration_ms = coalesce(
            duration_ms,
            CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE),
            CAST(json_extract_string(payload_json, '$.duration_seconds') AS DOUBLE) * 1000.0
        )
        WHERE duration_ms IS NULL
        """
    )

def ensure_store_schema(*, db_path: str | None = None) -> None:
    path = _db_path(db_path)
    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
        finally:
            connection.close()

def _ensure_search_runs(connection: duckdb.DuckDBPyConnection) -> None:
    """Create search_runs table with indexes if it doesn't exist."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RUNS_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            query VARCHAR NOT NULL,
            normalized_query VARCHAR,
            research_goal VARCHAR,
            num_results_requested INTEGER,
            rewrite_enabled BOOLEAN,
            session_id VARCHAR,
            tool_name VARCHAR DEFAULT 'web_search',
            duration_ms DOUBLE,
            final_result_count INTEGER,
            candidate_count INTEGER,
            has_more BOOLEAN,
            result_offset INTEGER,
            status VARCHAR,
            error_type VARCHAR,
            payload_json JSON
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_runs_run_key ON {_RUNS_TABLE_NAME}(run_key)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_runs_recorded_at ON {_RUNS_TABLE_NAME}(recorded_at)"
    )

def insert_search_run(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    """Insert a row into the search_runs table.

    Uses the same pattern as append_event()
    (threading.Lock, duckdb.connect, execute INSERT with VALUES).
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "run_key",
        "query",
        "normalized_query",
        "research_goal",
        "num_results_requested",
        "rewrite_enabled",
        "session_id",
        "tool_name",
        "duration_ms",
        "final_result_count",
        "candidate_count",
        "has_more",
        "result_offset",
        "status",
        "error_type",
        "payload_json",
    ]

    # Apply Python-level defaults for columns with SQL DEFAULT values
    # so DuckDB doesn't get an explicit None that bypasses the DEFAULT.
    if kwargs.get("tool_name") is None:
        kwargs["tool_name"] = "web_search"

    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    values = [kwargs.get(col) for col in columns]

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_search_runs(connection)
            connection.execute(
                f"""
                INSERT INTO {_RUNS_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                """,
                values,
            )
        finally:
            connection.close()

def _ensure_query_understanding(connection: duckdb.DuckDBPyConnection) -> None:
    """Create query_understanding table with index if it doesn't exist."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_QU_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            intent VARCHAR,
            confidence DOUBLE,
            should_decompose BOOLEAN,
            rationale VARCHAR,
            model VARCHAR,
            provider VARCHAR,
            duration_ms DOUBLE,
            fallback_used BOOLEAN,
            entities_count INTEGER,
            preserved_terms VARCHAR[],
            time_sensitivity VARCHAR,
            payload_json JSON
        )
        """
    )

def insert_query_understanding(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    """Insert a row into the query_understanding table.

    Uses the same pattern as insert_search_run()
    (threading.Lock, duckdb.connect, execute INSERT with VALUES).
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "run_key",
        "intent",
        "confidence",
        "should_decompose",
        "rationale",
        "model",
        "provider",
        "duration_ms",
        "fallback_used",
        "entities_count",
        "preserved_terms",
        "time_sensitivity",
        "payload_json",
    ]

    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    values = [kwargs.get(col) for col in columns]

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_query_understanding(connection)
            connection.execute(
                f"""
                INSERT INTO {_QU_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                """,
                values,
            )
        finally:
            connection.close()

def _ensure_query_rewrites(connection: duckdb.DuckDBPyConnection) -> None:
    """Create query_rewrites table with index if it doesn't exist."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_QR_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            variant_index INTEGER,
            branch_type VARCHAR,
            kind VARCHAR,
            target VARCHAR,
            query VARCHAR NOT NULL,
            weight DOUBLE,
            reason VARCHAR,
            max_results INTEGER,
            model VARCHAR,
            duration_ms DOUBLE,
            payload_json JSON
        )
        """
    )

def insert_query_rewrites(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    """Insert a row into the query_rewrites table.

    Uses the same pattern as insert_search_run()
    (threading.Lock, duckdb.connect, execute INSERT with VALUES).
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "run_key",
        "variant_index",
        "branch_type",
        "kind",
        "target",
        "query",
        "weight",
        "reason",
        "max_results",
        "model",
        "duration_ms",
        "payload_json",
    ]

    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    values = [kwargs.get(col) for col in columns]

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_query_rewrites(connection)
            connection.execute(
                f"""
                INSERT INTO {_QR_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                """,
                values,
            )
        finally:
            connection.close()

def _ensure_provider_calls(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_PC_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            provider VARCHAR NOT NULL,
            branch_index INTEGER,
            branch_query VARCHAR,
            num_results_requested INTEGER,
            num_results_returned INTEGER,
            duration_ms DOUBLE,
            error_code VARCHAR,
            error_message VARCHAR,
            http_status INTEGER,
            tokens_used INTEGER,
            cost_usd DOUBLE,
            payload_json JSON
        )
        '''
    )

def insert_provider_calls(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "provider", "branch_index", "branch_query",
        "num_results_requested", "num_results_returned", "duration_ms",
        "error_code", "error_message", "http_status",
        "tokens_used", "cost_usd", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_provider_calls(connection)
            connection.execute(
                f'''
                INSERT INTO {_PC_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_provider_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_PRC_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            provider VARCHAR NOT NULL,
            branch_index INTEGER,
            rank INTEGER,
            title VARCHAR,
            link VARCHAR,
            snippet VARCHAR,
            domain VARCHAR,
            score DOUBLE,
            published_date VARCHAR,
            payload_json JSON
        )
        '''
    )


def insert_provider_candidates(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "provider", "branch_index", "rank", "title",
        "link", "snippet", "domain", "score", "published_date", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_provider_candidates(connection)
            connection.execute(
                f'''
                INSERT INTO {_PRC_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_merged_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_MC_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            rank INTEGER,
            title VARCHAR,
            link VARCHAR,
            snippet VARCHAR,
            domain VARCHAR,
            rrf_score DOUBLE,
            provider_count INTEGER,
            providers VARCHAR[],
            overlap_flag BOOLEAN,
            payload_json JSON
        )
        '''
    )

def insert_merged_candidates(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "rank", "title", "link", "snippet", "domain",
        "rrf_score", "provider_count", "providers", "overlap_flag", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_merged_candidates(connection)
            connection.execute(
                f'''
                INSERT INTO {_MC_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_rerank_stages(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_RS_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            stage VARCHAR NOT NULL,
            provider VARCHAR,
            model VARCHAR,
            input_count INTEGER,
            output_count INTEGER,
            duration_ms DOUBLE,
            max_score DOUBLE,
            avg_score DOUBLE,
            score_threshold DOUBLE,
            instruction_present BOOLEAN,
            instruction_length INTEGER,
            query_type_hint VARCHAR,
            entity_overlap_enabled BOOLEAN,
            payload_json JSON
        )
        '''
    )


def insert_rerank_stages(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "stage", "provider", "model", "input_count", "output_count",
        "duration_ms", "max_score", "avg_score", "score_threshold",
        "instruction_present", "instruction_length", "query_type_hint",
        "entity_overlap_enabled", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_rerank_stages(connection)
            connection.execute(
                f'''
                INSERT INTO {_RS_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_rerank_candidates(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_RC_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            stage VARCHAR NOT NULL,
            link VARCHAR NOT NULL,
            rank_before INTEGER,
            rank_after INTEGER,
            score_before DOUBLE,
            score_after DOUBLE,
            score_after_relevance DOUBLE,
            score_after_recency DOUBLE,
            score_after_entity DOUBLE,
            recency_boost DOUBLE,
            entity_overlap_score DOUBLE,
            diversity_removed BOOLEAN,
            payload_json JSON
        )
        '''
    )

def insert_rerank_candidates(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "stage", "link", "rank_before", "rank_after",
        "score_before", "score_after", "score_after_relevance",
        "score_after_recency", "score_after_entity", "recency_boost",
        "entity_overlap_score", "diversity_removed", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_rerank_candidates(connection)
            connection.execute(
                f'''
                INSERT INTO {_RC_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_final_results(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_FR_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            rank INTEGER,
            title VARCHAR,
            link VARCHAR,
            snippet VARCHAR,
            domain VARCHAR,
            final_score DOUBLE,
            providers VARCHAR[],
            provider_count INTEGER,
            entities_count INTEGER,
            payload_json JSON
        )
        '''
    )

def insert_final_results(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "rank", "title", "link", "snippet", "domain",
        "final_score", "providers", "provider_count", "entities_count", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_final_results(connection)
            connection.execute(
                f'''
                INSERT INTO {_FR_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_search_quality_scores(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_SQS_TABLE_NAME} (
            run_key VARCHAR NOT NULL PRIMARY KEY,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            provider_overlap_rate DOUBLE,
            domain_diversity_count INTEGER,
            domain_diversity_ratio DOUBLE,
            rerank_compression_ratio DOUBLE,
            avg_rrf_score DOUBLE,
            top_score DOUBLE,
            p95_score DOUBLE,
            rewrite_variant_count INTEGER,
            provider_count INTEGER,
            branch_count INTEGER,
            total_candidates_input INTEGER,
            total_candidates_merged INTEGER,
            total_candidates_reranked INTEGER,
            total_final_results INTEGER,
            payload_json JSON
        )
        '''
    )

def insert_search_quality_scores(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "provider_overlap_rate", "domain_diversity_count",
        "domain_diversity_ratio", "rerank_compression_ratio", "avg_rrf_score",
        "top_score", "p95_score", "rewrite_variant_count", "provider_count",
        "branch_count", "total_candidates_input", "total_candidates_merged",
        "total_candidates_reranked", "total_final_results", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_search_quality_scores(connection)
            connection.execute(
                f'''
                INSERT INTO {_SQS_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ON CONFLICT DO NOTHING
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_summary_provider_daily(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_SUM_PVD_TABLE_NAME} (
            day DATE NOT NULL,
            provider VARCHAR NOT NULL,
            query_count BIGINT,
            avg_results_returned DOUBLE,
            p50_results_returned DOUBLE,
            avg_latency_ms DOUBLE,
            p50_latency_ms DOUBLE,
            p95_latency_ms DOUBLE,
            error_rate DOUBLE,
            distinct_queries BIGINT,
            PRIMARY KEY (day, provider)
        )
        '''
    )

def _ensure_summary_intent_daily(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_SUM_ID_TABLE_NAME} (
            day DATE NOT NULL,
            intent VARCHAR NOT NULL,
            query_count BIGINT,
            avg_confidence DOUBLE,
            decomposition_rate DOUBLE,
            fallback_rate DOUBLE,
            avg_rewrite_variants DOUBLE,
            PRIMARY KEY (day, intent)
        )
        '''
    )

def _ensure_summary_rerank_daily(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_SUM_RD_TABLE_NAME} (
            day DATE NOT NULL,
            stage VARCHAR NOT NULL,
            provider VARCHAR,
            runs_count BIGINT,
            avg_compression_ratio DOUBLE,
            avg_max_score DOUBLE,
            p50_latency_ms DOUBLE,
            p95_latency_ms DOUBLE,
            entity_overlap_runs BIGINT,
            PRIMARY KEY (day, stage, provider)
        )
        '''
    )

def _ensure_summary_quality_daily(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_SUM_QD_TABLE_NAME} (
            day DATE NOT NULL,
            avg_overlap_rate DOUBLE,
            avg_domain_diversity DOUBLE,
            avg_domain_diversity_ratio DOUBLE,
            avg_compression_ratio DOUBLE,
            avg_top_score DOUBLE,
            PRIMARY KEY (day)
        )
        '''
    )

def _ensure_judge_evaluations(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_JE_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            tool_name VARCHAR,
            judge_model VARCHAR,
            relevance_score DOUBLE,
            accuracy_score DOUBLE,
            completeness_score DOUBLE,
            source_quality_score DOUBLE,
            overall_score DOUBLE,
            rationale VARCHAR,
            duration_ms DOUBLE,
            tokens_used INTEGER,
            cost_usd DOUBLE,
            payload_json JSON
        )
        '''
    )

def insert_judge_evaluation(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "tool_name", "judge_model", "relevance_score",
        "accuracy_score", "completeness_score", "source_quality_score",
        "overall_score", "rationale", "duration_ms", "tokens_used",
        "cost_usd", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_judge_evaluations(connection)
            connection.execute(
                f'''
                INSERT INTO {_JE_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def _ensure_ab_experiments(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_ABE_TABLE_NAME} (
            experiment_id VARCHAR NOT NULL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            layer VARCHAR NOT NULL,
            variant_a VARCHAR NOT NULL,
            variant_b VARCHAR NOT NULL,
            allocation_rate DOUBLE NOT NULL DEFAULT 0.5,
            status VARCHAR NOT NULL DEFAULT 'active',
            start_date DATE,
            end_date DATE,
            min_sample_size INTEGER,
            payload_json JSON
        )
        '''
    )

def _ensure_ab_shadow_runs(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_ABS_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            experiment_id VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            layer VARCHAR NOT NULL,
            duration_ms DOUBLE,
            judge_score DOUBLE,
            tokens_used INTEGER,
            cost_usd DOUBLE,
            error_type VARCHAR,
            payload_json JSON
        )
        '''
    )


def _ensure_ab_experiment_variants(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_ABV_TABLE_NAME} (
            variant_id VARCHAR NOT NULL PRIMARY KEY,
            experiment_id VARCHAR NOT NULL,
            variant_name VARCHAR NOT NULL,
            description VARCHAR,
            config_json JSON,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        '''
    )


def _ensure_ab_assignments(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_ABA_TABLE_NAME} (
            assignment_id VARCHAR NOT NULL PRIMARY KEY,
            experiment_id VARCHAR NOT NULL,
            run_key VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload_json JSON
        )
        '''
    )


def _ensure_ab_results(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f'''
        CREATE TABLE IF NOT EXISTS {_ABR_TABLE_NAME} (
            result_id VARCHAR NOT NULL PRIMARY KEY,
            experiment_id VARCHAR NOT NULL,
            run_key VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            primary_metric DOUBLE,
            secondary_metric DOUBLE,
            duration_ms DOUBLE,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload_json JSON
        )
        '''
    )


def insert_ab_experiment(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "experiment_id", "layer", "variant_a", "variant_b",
        "allocation_rate", "status", "start_date", "end_date",
        "min_sample_size", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_ab_experiments(connection)
            connection.execute(
                f'''
                INSERT INTO {_ABE_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ON CONFLICT DO NOTHING
                ''',
                values,
            )
        finally:
            connection.close()

def insert_ab_shadow_run(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "run_key", "experiment_id", "variant", "layer",
        "duration_ms", "judge_score", "tokens_used", "cost_usd",
        "error_type", "payload_json",
    ]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    values = [kwargs.get(col) for col in columns]
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_ab_shadow_runs(connection)
            connection.execute(
                f'''
                INSERT INTO {_ABS_TABLE_NAME} ({col_list})
                VALUES ({placeholders})
                ''',
                values,
            )
        finally:
            connection.close()

def append_event(
    event_name: str,
    payload: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Append a normalized observability payload to DuckDB.

    The store is best-effort and is disabled when
    `ANALYTICS_ENABLED=false`.
    """

    if not settings.analytics_enabled:
        return

    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = (
        str(uuid.uuid4()),
        event_name,
        _run_key(payload),
        _event_value(payload, "tool_name"),
        _phase(event_name),
        _event_value(payload, "query"),
        _event_value(payload, "normalized_query"),
        _event_value(payload, "research_goal"),
        _provider_value(payload),
        _event_value(payload, "model"),
        _duration_ms_value(payload),
        _input_count_value(payload),
        _output_count_value(payload),
        _event_value(payload, "trace_id"),
        _event_value(payload, "span_id"),
        _event_value(payload, "cache_hit"),
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_schema(connection)
            connection.execute(
                f"""
                INSERT INTO {_TABLE_NAME} (
                    event_id,
                    event_name,
                    recorded_at,
                    run_key,
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
                    payload_json
                ) VALUES (
                    ?,
                    ?,
                    CURRENT_TIMESTAMP,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                record,
            )
        finally:
            connection.close()
