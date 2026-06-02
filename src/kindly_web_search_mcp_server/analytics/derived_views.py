"""Derived DuckDB/MotherDuck analytics views for caches, middleware, and content."""

from __future__ import annotations


def build_cache_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_cache_lookups AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            coalesce(json_extract_string(payload_json, '$.cache_type'), 'unknown') AS cache_type,
            coalesce(json_extract_string(payload_json, '$.lookup_status'), 'unknown') AS lookup_status,
            lower(coalesce(json_extract_string(payload_json, '$.hit'), 'false')) AS cache_hit_text,
            CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE) AS duration_ms,
            CAST(json_extract_string(payload_json, '$.age_seconds') AS DOUBLE) AS age_seconds,
            CAST(json_extract_string(payload_json, '$.ttl_seconds') AS DOUBLE) AS ttl_seconds,
            CAST(json_extract_string(payload_json, '$.similarity_score') AS DOUBLE) AS similarity_score,
            CAST(json_extract_string(payload_json, '$.vector_distance') AS DOUBLE) AS vector_distance,
            coalesce(provider, json_extract_string(payload_json, '$.provider')) AS provider,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name')) AS tool_name,
            query,
            normalized_query,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name = 'search.cache.lookup'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_cache_stores AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            coalesce(json_extract_string(payload_json, '$.cache_type'), 'unknown') AS cache_type,
            coalesce(json_extract_string(payload_json, '$.store_status'), 'unknown') AS store_status,
            CAST(json_extract_string(payload_json, '$.ttl_seconds') AS DOUBLE) AS ttl_seconds,
            CAST(json_extract_string(payload_json, '$.response_size') AS DOUBLE) AS response_size,
            CAST(json_extract_string(payload_json, '$.word_count') AS DOUBLE) AS word_count,
            CAST(json_extract_string(payload_json, '$.metadata_present') AS BOOLEAN) AS metadata_present,
            coalesce(provider, json_extract_string(payload_json, '$.provider')) AS provider,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name')) AS tool_name,
            query,
            normalized_query,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name = 'search.cache.store'
        """,
    ]


def build_middleware_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_middleware_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            CASE
                WHEN event_name LIKE 'middleware.rate_limit.%' THEN 'rate_limit'
                WHEN event_name LIKE 'middleware.expensive_tool.%' THEN 'expensive_tool'
                ELSE 'other'
            END AS middleware_kind,
            coalesce(json_extract_string(payload_json, '$.tool_name'), tool_name, 'unknown') AS tool_name,
            coalesce(json_extract_string(payload_json, '$.bucket'), 'unknown') AS bucket,
            CAST(json_extract_string(payload_json, '$.waited_seconds') AS DOUBLE) AS waited_seconds,
            CAST(json_extract_string(payload_json, '$.attempt_count') AS INTEGER) AS attempt_count,
            coalesce(json_extract_string(payload_json, '$.session_id'), trace_id, run_key) AS session_id,
            coalesce(json_extract_string(payload_json, '$.provider'), provider, 'unknown') AS provider,
            json_extract_string(payload_json, '$.scope') AS scope,
            json_extract_string(payload_json, '$.error_type') AS error_type,
            json_extract_string(payload_json, '$.action') AS action,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name LIKE 'middleware.%'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_session_activity AS
        SELECT
            event_id,
            recorded_at,
            coalesce(json_extract_string(payload_json, '$.session_id'), run_key, trace_id, event_id) AS session_id,
            event_name,
            CASE
                WHEN event_name = 'session.started' THEN 'started'
                WHEN event_name = 'session.activity' THEN 'activity'
                WHEN event_name = 'session.expired' THEN 'expired'
                ELSE 'unknown'
            END AS session_state,
            json_extract_string(payload_json, '$.tool_name') AS tool_name,
            CAST(json_extract_string(payload_json, '$.tool_count') AS INTEGER) AS tool_count,
            CAST(json_extract_string(payload_json, '$.session_timeout_seconds') AS DOUBLE) AS session_timeout_seconds,
            json_extract_string(payload_json, '$.scope') AS scope,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name LIKE 'session.%'
        """,
    ]


def build_content_and_error_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_content_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            CASE
                WHEN event_name = 'content.stage.resolution' THEN 'resolution'
                WHEN event_name = 'content.stage.fallback' THEN 'fallback'
                WHEN event_name = 'content.stage.error' THEN 'error'
                WHEN event_name = 'content.status.classified' THEN 'classification'
                ELSE 'other'
            END AS content_event_kind,
            json_extract_string(payload_json, '$.stage') AS stage,
            json_extract_string(payload_json, '$.status') AS status,
            json_extract_string(payload_json, '$.reason') AS reason,
            json_extract_string(payload_json, '$.content_status') AS content_status,
            CAST(json_extract_string(payload_json, '$.success') AS BOOLEAN) AS success,
            CAST(json_extract_string(payload_json, '$.size_bytes') AS DOUBLE) AS size_bytes,
            CAST(json_extract_string(payload_json, '$.duration_seconds') AS DOUBLE) AS duration_seconds,
            CAST(json_extract_string(payload_json, '$.word_count') AS INTEGER) AS word_count,
            json_extract_string(payload_json, '$.extraction_method') AS extraction_method,
            CAST(json_extract_string(payload_json, '$.cacheable') AS BOOLEAN) AS cacheable,
            CAST(json_extract_string(payload_json, '$.bad_char_ratio') AS DOUBLE) AS bad_char_ratio,
            CAST(json_extract_string(payload_json, '$.cookie_ratio') AS DOUBLE) AS cookie_ratio,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name LIKE 'content.%'
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_error_events AS
        SELECT
            event_id,
            recorded_at,
            coalesce(run_key, trace_id, event_id) AS run_key,
            event_name,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name')) AS tool_name,
            coalesce(provider, json_extract_string(payload_json, '$.provider'), json_extract_string(payload_json, '$.provider_name')) AS provider,
            coalesce(json_extract_string(payload_json, '$.error_type'), 'unknown') AS error_type,
            json_extract_string(payload_json, '$.status_code') AS status_code,
            json_extract_string(payload_json, '$.retry_after') AS retry_after,
            json_extract_string(payload_json, '$.action') AS action,
            payload_json
        FROM {target}.analytics_event_raw
        WHERE event_name LIKE '%.error' OR event_name = 'tool.error.classified'
        """,
    ]


def build_derived_view_sql(target: str) -> list[str]:
    return [
        *build_cache_view_sql(target),
        *build_middleware_view_sql(target),
        *build_content_and_error_view_sql(target),
    ]
