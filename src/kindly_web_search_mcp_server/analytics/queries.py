"""Guarded read-only analytics query builder for local DuckDB and MotherDuck."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb
import pyarrow as pa

from ..settings import settings
from .formatting import json_safe_rows
from .local_queries import run_local_analytics_query
from .motherduck_sync import (
    _attach_name,
    _duckdb_config,
    _load_motherduck,
    _motherduck_database,
    _quote_ident,
)

AnalyticsScope = Literal["local", "motherduck"]


@dataclass(frozen=True)
class AnalyticsQueryPlan:
    sql: str
    view_prefix: str
    rationale: str


def _db_path(db_path: str | None = None) -> Path:
    return Path(db_path or settings.analytics_duckdb_path)


def _normalize_view_prefix(view_prefix: str) -> str:
    prefix = view_prefix.strip()
    if not prefix.endswith("."):
        prefix = f"{prefix}."
    return prefix


def _analytics_connection_and_prefix(
    path: Path,
    *,
    scope: AnalyticsScope,
) -> tuple[duckdb.DuckDBPyConnection, str]:
    if scope == "motherduck":
        database = _motherduck_database()
        attach = _attach_name(database)
        schema = _quote_ident("web_search_analytics")
        connection = duckdb.connect(config=_duckdb_config())  # type: ignore[arg-type]
        _load_motherduck(connection)
        connection.execute(f"ATTACH 'md:{database}' AS {_quote_ident(attach)}")
        return connection, _normalize_view_prefix(f"{_quote_ident(attach)}.{schema}")

    return duckdb.connect(str(path), read_only=True), "main."


def _is_cache_question(question: str) -> bool:
    return "cache" in question


def _is_provider_question(question: str) -> bool:
    return (
        "provider" in question
        or "searxng" in question
        or "brave" in question
        or "tavily" in question
    )


def _is_session_question(question: str) -> bool:
    return "session" in question or "middleware" in question or "rate limit" in question


def _is_error_question(question: str) -> bool:
    return (
        "error" in question
        or "failure" in question
        or "timeout" in question
        or "exception" in question
        or "blocked" in question
    )


def _is_fetch_question(question: str) -> bool:
    return "fetch" in question or "window" in question or "page content" in question


def _is_content_question(question: str) -> bool:
    return (
        "content" in question
        or "classification" in question
        or "markdown" in question
        or "blocked" in question
    )


def _is_eval_question(question: str) -> bool:
    return "eval" in question or "quality score" in question or "suite" in question


def _is_recent_events_question(question: str) -> bool:
    return (
        "recent" in question
        or "latest" in question
        or "timeline" in question
        or "activity" in question
        or "event" in question
    )


def build_analytics_query_plan(
    question: str,
    *,
    view_prefix: str = "main.",
    max_rows: int = 100,
) -> AnalyticsQueryPlan:
    limit = max(1, min(int(max_rows), 500))
    prefix = _normalize_view_prefix(view_prefix)
    q = question.lower().strip()

    if _is_cache_question(q):
        sql = f"""
            SELECT
                cache_type,
                lookup_status,
                COUNT(*) AS calls,
                SUM(CASE WHEN cache_hit_text = 'true' THEN 1 ELSE 0 END) AS hits,
                SUM(CASE WHEN cache_hit_text = 'false' THEN 1 ELSE 0 END) AS misses,
                AVG(duration_ms) AS avg_duration_ms,
                AVG(similarity_score) AS avg_similarity_score
            FROM {prefix}vw_cache_lookups
            GROUP BY 1, 2
            ORDER BY calls DESC, cache_type, lookup_status
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="cache")

    if _is_provider_question(q):
        sql = f"""
            SELECT
                provider,
                COUNT(*) AS rows,
                COUNT(DISTINCT run_key) AS runs,
                AVG(score) AS avg_score,
                AVG(provider_count) AS avg_provider_count
            FROM {prefix}vw_provider_results
            GROUP BY 1
            ORDER BY rows DESC, provider
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="provider")

    if _is_session_question(q):
        sql = f"""
            SELECT
                middleware_kind,
                tool_name,
                bucket,
                COUNT(*) AS rows,
                COUNT(DISTINCT session_id) AS sessions,
                AVG(waited_seconds) AS avg_waited_seconds,
                AVG(attempt_count) AS avg_attempt_count
            FROM {prefix}vw_middleware_events
            GROUP BY 1, 2, 3
            ORDER BY rows DESC, middleware_kind, tool_name, bucket
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="middleware")

    if _is_error_question(q):
        sql = f"""
            SELECT
                event_name,
                tool_name,
                provider,
                error_type,
                COUNT(*) AS rows,
                AVG(duration_ms) AS avg_duration_ms
            FROM {prefix}vw_error_events
            GROUP BY 1, 2, 3, 4
            ORDER BY rows DESC, event_name, tool_name, provider, error_type
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="error")

    if _is_eval_question(q):
        sql = f"""
            SELECT
                suite_name,
                target_tool,
                COUNT(*) AS cases,
                COUNT(*) FILTER (WHERE passes > 0) AS cases_with_passes,
                AVG(avg_score) AS avg_score
            FROM {prefix}vw_eval_provider_quality
            GROUP BY 1, 2
            ORDER BY cases DESC, suite_name, target_tool
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="eval")

    if _is_fetch_question(q):
        sql = f"""
            SELECT
                fetch_backend,
                status,
                COUNT(*) AS rows,
                AVG(page_char_count) AS avg_page_chars,
                AVG(word_count) AS avg_word_count,
                SUM(CASE WHEN window_has_more THEN 1 ELSE 0 END) AS partial_windows
            FROM {prefix}vw_fetch_events
            GROUP BY 1, 2
            ORDER BY rows DESC, fetch_backend, status
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="fetch")

    if _is_content_question(q):
        sql = f"""
            SELECT
                content_event_kind,
                stage,
                status,
                reason,
                COUNT(*) AS rows,
                AVG(word_count) AS avg_word_count,
                AVG(size_bytes) AS avg_size_bytes
            FROM {prefix}vw_content_events
            GROUP BY 1, 2, 3, 4
            ORDER BY rows DESC, content_event_kind, stage
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="content")

    if _is_recent_events_question(q):
        sql = f"""
            SELECT
                recorded_at,
                event_name,
                tool_name,
                provider,
                cache_hit,
                query,
                normalized_query
            FROM {prefix}vw_events
            ORDER BY recorded_at DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="events")

    raise ValueError(
        "Could not classify the analytics question. "
        "Supported topics: cache, provider, middleware/session, error, eval, fetch, content, recent events."
    )


def run_analytics_query(
    question: str,
    *,
    scope: AnalyticsScope = "local",
    max_rows: int = 100,
    db_path: str | None = None,
) -> dict[str, object]:
    path = _db_path(db_path)
    if scope == "local":
        return run_local_analytics_query(
            question,
            max_rows=max_rows,
            db_path=str(path),
        )

    connection, view_prefix = _analytics_connection_and_prefix(path, scope=scope)
    try:
        plan = build_analytics_query_plan(
            question,
            view_prefix=view_prefix,
            max_rows=max_rows,
        )
        table = connection.execute(plan.sql).to_arrow_table()
    finally:
        connection.close()

    return {
        "question": question,
        "scope": scope,
        "view_prefix": view_prefix,
        "rationale": plan.rationale,
        "sql": plan.sql.strip(),
        "row_count": table.num_rows,
        "rows": json_safe_rows(table.to_pylist()),
    }


def run_analytics_query_table(
    question: str,
    *,
    scope: AnalyticsScope = "local",
    max_rows: int = 100,
    db_path: str | None = None,
) -> pa.Table:
    result = run_analytics_query(
        question,
        scope=scope,
        max_rows=max_rows,
        db_path=db_path,
    )
    return pa.Table.from_pylist(result["rows"])  # type: ignore[arg-type]
