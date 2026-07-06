from __future__ import annotations

from pathlib import Path

import duckdb

from ..settings import settings
from .formatting import json_safe_rows


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _limit(max_rows: int) -> int:
    return max(1, min(int(max_rows), 500))


def _cache_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            coalesce(json_extract_string(payload_json, '$.cache_type'), 'unknown') AS cache_type,
            coalesce(json_extract_string(payload_json, '$.lookup_status'), 'unknown') AS lookup_status,
            COUNT(*) AS calls,
            SUM(CASE WHEN lower(coalesce(json_extract_string(payload_json, '$.hit'), 'false')) = 'true' THEN 1 ELSE 0 END) AS hits,
            SUM(CASE WHEN lower(coalesce(json_extract_string(payload_json, '$.hit'), 'false')) = 'false' THEN 1 ELSE 0 END) AS misses,
            AVG(CAST(json_extract_string(payload_json, '$.duration_ms') AS DOUBLE)) AS avg_duration_ms,
            AVG(CAST(json_extract_string(payload_json, '$.similarity_score') AS DOUBLE)) AS avg_similarity_score
        FROM search_events
        WHERE event_name = 'search.cache.lookup'
        GROUP BY 1, 2
        ORDER BY calls DESC, cache_type, lookup_status
        LIMIT {limit}
    """
    return sql, "cache"


def _provider_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            coalesce(provider, json_extract_string(payload_json, '$.provider_name'), 'unknown') AS provider,
            COUNT(*) AS rows,
            COUNT(DISTINCT run_key) AS runs,
            COUNT(*) FILTER (WHERE event_name = 'provider.search.result') AS result_events,
            COUNT(*) FILTER (WHERE event_name = 'provider.search.error') AS error_events,
            AVG(duration_ms) AS avg_duration_ms,
            AVG(output_count) FILTER (WHERE event_name = 'provider.search.result') AS avg_output_count
        FROM search_events
        WHERE event_name IN ('provider.search.result', 'provider.search.error')
        GROUP BY 1
        ORDER BY rows DESC, provider
        LIMIT {limit}
    """
    return sql, "provider"


def _middleware_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            CASE
                WHEN event_name LIKE 'middleware.rate_limit.%' THEN 'rate_limit'
                WHEN event_name LIKE 'middleware.expensive_tool.%' THEN 'expensive_tool'
                ELSE 'other'
            END AS middleware_kind,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name'), 'unknown') AS tool_name,
            coalesce(json_extract_string(payload_json, '$.bucket'), 'unknown') AS bucket,
            COUNT(*) AS rows,
            COUNT(DISTINCT coalesce(json_extract_string(payload_json, '$.session_id'), trace_id, run_key, event_id)) AS sessions,
            AVG(CAST(json_extract_string(payload_json, '$.waited_seconds') AS DOUBLE)) AS avg_waited_seconds,
            AVG(CAST(json_extract_string(payload_json, '$.attempt_count') AS DOUBLE)) AS avg_attempt_count
        FROM search_events
        WHERE event_name LIKE 'middleware.%'
        GROUP BY 1, 2, 3
        ORDER BY rows DESC, middleware_kind, tool_name, bucket
        LIMIT {limit}
    """
    return sql, "middleware"


def _error_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            event_name,
            coalesce(tool_name, json_extract_string(payload_json, '$.tool_name'), 'unknown') AS tool_name,
            coalesce(provider, json_extract_string(payload_json, '$.provider'), json_extract_string(payload_json, '$.provider_name'), 'unknown') AS provider,
            coalesce(json_extract_string(payload_json, '$.error_type'), 'unknown') AS error_type,
            COUNT(*) AS rows,
            AVG(duration_ms) AS avg_duration_ms
        FROM search_events
        WHERE event_name LIKE '%.error' OR event_name = 'tool.error.classified'
        GROUP BY 1, 2, 3, 4
        ORDER BY rows DESC, event_name, tool_name, provider, error_type
        LIMIT {limit}
    """
    return sql, "error"


def _eval_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            experiment_id AS suite_name,
            layer AS target_tool,
            COUNT(*) AS cases,
            COUNT(*) FILTER (WHERE judge_score >= 0.7) AS cases_with_passes,
            AVG(judge_score) AS avg_score
        FROM ab_shadow_runs
        GROUP BY 1, 2
        ORDER BY cases DESC, suite_name, target_tool
        LIMIT {limit}
    """
    return sql, "eval"


def _fetch_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            coalesce(json_extract_string(payload_json, '$.fetch_backend'), 'unknown') AS fetch_backend,
            coalesce(json_extract_string(payload_json, '$.status'), 'unknown') AS status,
            COUNT(*) AS rows,
            AVG(LENGTH(coalesce(json_extract_string(payload_json, '$.page_content'), ''))) AS avg_page_chars,
            AVG(CAST(json_extract_string(payload_json, '$.word_count') AS DOUBLE)) AS avg_word_count,
            SUM(CASE WHEN CAST(json_extract_string(payload_json, '$.window.has_more') AS BOOLEAN) THEN 1 ELSE 0 END) AS partial_windows
        FROM search_events
        WHERE event_name IN ('tool.get_content.response', 'tool.batch_get_content.response')
        GROUP BY 1, 2
        ORDER BY rows DESC, fetch_backend, status
        LIMIT {limit}
    """
    return sql, "fetch"


def _content_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
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
            COUNT(*) AS rows,
            AVG(CAST(json_extract_string(payload_json, '$.word_count') AS DOUBLE)) AS avg_word_count,
            AVG(CAST(json_extract_string(payload_json, '$.size_bytes') AS DOUBLE)) AS avg_size_bytes
        FROM search_events
        WHERE event_name LIKE 'content.%'
        GROUP BY 1, 2, 3, 4
        ORDER BY rows DESC, content_event_kind, stage
        LIMIT {limit}
    """
    return sql, "content"


def _recent_events_query(limit: int) -> tuple[str, str]:
    sql = f"""
        SELECT
            recorded_at,
            event_name,
            tool_name,
            provider,
            cache_hit,
            query,
            normalized_query
        FROM search_events
        ORDER BY recorded_at DESC
        LIMIT {limit}
    """
    return sql, "events"


def build_local_analytics_query_sql(question: str, *, max_rows: int = 100) -> tuple[str, str]:
    limit = _limit(max_rows)
    q = question.lower().strip()
    if "cache" in q:
        return _cache_query(limit)
    if "provider" in q or "searxng" in q or "brave" in q or "tavily" in q:
        return _provider_query(limit)
    if "session" in q or "middleware" in q or "rate limit" in q:
        return _middleware_query(limit)
    if "error" in q or "failure" in q or "timeout" in q or "exception" in q or "blocked" in q:
        return _error_query(limit)
    if "eval" in q or "quality score" in q or "suite" in q:
        return _eval_query(limit)
    if "fetch" in q or "window" in q or "page content" in q:
        return _fetch_query(limit)
    if "content" in q or "classification" in q or "markdown" in q or "blocked" in q:
        return _content_query(limit)
    if "recent" in q or "latest" in q or "timeline" in q or "activity" in q or "event" in q:
        return _recent_events_query(limit)
    raise ValueError(
        "Could not classify the analytics question. "
        "Supported topics: cache, provider, middleware/session, error, eval, fetch, content, recent events."
    )


def run_local_analytics_query(
    question: str,
    *,
    max_rows: int = 100,
    db_path: str | None = None,
) -> dict[str, object]:
    path = _db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    connection = duckdb.connect(str(path), read_only=True)
    try:
        sql, rationale = build_local_analytics_query_sql(question, max_rows=max_rows)
        table = connection.execute(sql).to_arrow_table()
    finally:
        connection.close()

    return {
        "question": question,
        "scope": "local",
        "view_prefix": "main.",
        "rationale": rationale,
        "sql": sql.strip(),
        "row_count": table.num_rows,
        "rows": json_safe_rows(table.to_pylist()),
    }
