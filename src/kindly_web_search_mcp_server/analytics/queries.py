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


# ---------------------------------------------------------------------------
# Question classifiers
# ---------------------------------------------------------------------------


def _is_provider_question(question: str) -> bool:
    return (
        "provider" in question
        or "searxng" in question
        or "brave" in question
        or "tavily" in question
    )


def _is_error_question(question: str) -> bool:
    return (
        "error" in question
        or "failure" in question
        or "timeout" in question
        or "exception" in question
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


def _is_rerank_question(question: str) -> bool:
    return (
        "rerank" in question
        or "cross encode" in question
        or "bi-encode" in question
        or "rankllm" in question
    )


def _is_latency_question(question: str) -> bool:
    return (
        "latency" in question
        or "speed" in question
        or "duration" in question
        or "fast" in question
        or "slow" in question
    )


def _is_run_quality_question(question: str) -> bool:
    return ("quality" in question or "score" in question or "verdict" in question) and (
        "run" in question or "result" in question or "outcome" in question
    )


def _is_quick_search_question(question: str) -> bool:
    return "quick" in question or "parallel" in question or "citation" in question


def _is_gemini_search_question(question: str) -> bool:
    return "gemini" in question or "grounding" in question


def _is_code_search_question(question: str) -> bool:
    return (
        "code search" in question
        or "code_search" in question
        or "grepapp" in question
        or "sourcegraph" in question
        or "github code" in question
        or "diagnostic" in question
        or "repository discovery" in question
    )


def _is_content_question(question: str) -> bool:
    return (
        "summary" in question
        or "summaries" in question
        or "content summary" in question
        or "content operation" in question
    )


def _is_coverage_question(question: str) -> bool:
    return (
        "coverage" in question
        or "tool call" in question
        or "linkage" in question
        or "cross tool" in question
    )


# ---------------------------------------------------------------------------
# Query plan builder
# ---------------------------------------------------------------------------


def build_analytics_query_plan(
    question: str,
    *,
    view_prefix: str = "main.",
    max_rows: int = 100,
) -> AnalyticsQueryPlan:
    limit = max(1, min(int(max_rows), 500))
    prefix = _normalize_view_prefix(view_prefix)
    q = question.lower().strip()

    if _is_code_search_question(q):
        sql = f"""
            SELECT
                provider,
                outcome,
                COUNT(*) AS total_responses,
                SUM(hit_count) AS total_hits_returned,
                ROUND(AVG(hit_count), 2) AS avg_hits_per_response,
                SUM(request_count) AS total_requests,
                ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms,
                COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count
            FROM {prefix}code_search_providers
            GROUP BY provider, outcome
            ORDER BY total_responses DESC, provider
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="code_search")

    if _is_quick_search_question(q):
        sql = f"""
            SELECT
                COALESCE(client_model, 'unspecified') AS client_model,
                status,
                COUNT(*) AS total_runs,
                ROUND(AVG(total_citations), 2) AS avg_citations,
                SUM(total_citations) AS total_citations,
                ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count
            FROM {prefix}quick_web_search_runs
            GROUP BY client_model, status
            ORDER BY total_runs DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="quick_search")

    if _is_gemini_search_question(q):
        sql = f"""
            SELECT
                COALESCE(model_used, 'unknown') AS model_used,
                COALESCE(mode, 'standard') AS mode,
                status,
                COUNT(*) AS total_runs,
                ROUND(AVG(grounding_chunks_count), 2) AS avg_grounding_chunks,
                ROUND(AVG(web_search_queries_count), 2) AS avg_web_search_queries,
                ROUND(AVG(prompt_tokens), 1) AS avg_prompt_tokens,
                ROUND(AVG(completion_tokens), 1) AS avg_completion_tokens,
                ROUND(AVG(duration_ms), 2) AS avg_duration_ms
            FROM {prefix}gemini_search_runs
            GROUP BY model_used, mode, status
            ORDER BY total_runs DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="gemini_search")

    if _is_content_question(q):
        sql = f"""
            SELECT
                COALESCE(backend, 'unspecified') AS backend,
                COALESCE(model_used, 'unspecified') AS model_used,
                is_batch,
                is_stub,
                status,
                COUNT(*) AS total_summaries,
                ROUND(AVG(summary_length_chars), 0) AS avg_summary_chars,
                ROUND(AVG(key_points_count), 2) AS avg_key_points,
                ROUND(AVG(important_entities_count), 2) AS avg_entities,
                ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms
            FROM {prefix}content_summaries
            GROUP BY backend, model_used, is_batch, is_stub, status
            ORDER BY total_summaries DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="content_summaries")

    if _is_coverage_question(q):
        sql = f"""
            SELECT
                tool_name,
                COUNT(*) AS total_events,
                COUNT(*) FILTER (WHERE phase = 'request') AS request_events,
                COUNT(*) FILTER (WHERE phase = 'response') AS response_events,
                COUNT(*) FILTER (WHERE phase = 'error') AS error_events,
                COUNT(DISTINCT tool_call_id) AS distinct_tool_calls,
                ROUND(100.0 * COUNT(*) FILTER (WHERE phase IN ('response', 'error'))
                    / NULLIF(COUNT(*) FILTER (WHERE phase = 'request'), 0), 2) AS terminal_event_rate_pct,
                ROUND(AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 2) AS avg_duration_ms
            FROM {prefix}tool_calls
            GROUP BY tool_name
            ORDER BY total_events DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="tool_call_coverage")

    if _is_rerank_question(q):
        sql = f"""
            SELECT
                stage,
                provider,
                model,
                COUNT(*) AS stage_runs,
                SUM(input_count) AS total_input_count,
                SUM(output_count) AS total_output_count,
                ROUND(100.0 * SUM(output_count) / NULLIF(SUM(input_count), 0), 1) AS survival_rate_pct,
                ROUND(AVG(duration_ms), 1) AS avg_duration_ms,
                ROUND(AVG(max_score), 4) AS avg_max_score,
                ROUND(AVG(avg_score), 4) AS avg_score,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
                MODE(error_type) AS most_common_error,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens
            FROM {prefix}rerank_stages
            WHERE stage IN ('bi_encoder', 'cross_encoder', 'rankllm')
            GROUP BY 1, 2, 3
            ORDER BY stage_runs DESC, stage, provider, model
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="rerank")

    if _is_latency_question(q):
        sql = f"""
            SELECT
                CASE
                    WHEN duration_ms < 2000  THEN '0-2s'
                    WHEN duration_ms < 5000  THEN '2-5s'
                    WHEN duration_ms < 10000 THEN '5-10s'
                    WHEN duration_ms < 20000 THEN '10-20s'
                    WHEN duration_ms < 30000 THEN '20-30s'
                    ELSE '30s+'
                END AS latency_bucket,
                COUNT(*) AS run_count,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
                ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
                ROUND(quantile_cont(duration_ms, 0.95), 0) AS p95_duration_ms,
                ROUND(AVG(final_result_count), 1) AS avg_result_count,
                COUNT(DISTINCT run_key) AS distinct_runs
            FROM {prefix}search_runs
            GROUP BY 1
            ORDER BY MIN(duration_ms)
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="latency")

    if _is_run_quality_question(q):
        sql = f"""
            SELECT
                suite_name,
                COUNT(DISTINCT target_tool) AS tools_tested,
                SUM(cases) AS total_cases,
                SUM(passes) AS total_passes,
                SUM(fails) AS total_fails,
                ROUND(100.0 * SUM(passes) / NULLIF(SUM(cases), 0), 1) AS pass_rate_pct,
                ROUND(AVG(avg_score), 3) AS avg_score,
                MIN(avg_score) AS min_score,
                MAX(avg_score) AS max_score
            FROM {prefix}vw_eval_provider_quality
            GROUP BY 1
            ORDER BY total_cases DESC, suite_name
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="run_quality")

    if _is_provider_question(q):
        sql = f"""
            SELECT
                provider,
                COUNT(*) AS total_calls,
                COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0), 1) AS success_rate_pct,
                ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
                ROUND(quantile_cont(latency_ms, 0.95), 0) AS p95_latency_ms,
                SUM(num_results_returned) AS total_results_returned,
                COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS error_count,
                MODE(error_type) AS most_common_error
            FROM {prefix}provider_calls
            GROUP BY 1
            ORDER BY total_calls DESC, provider
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="provider")

    if _is_error_question(q):
        sql = f"""
            SELECT
                provider,
                error_type,
                COUNT(*) AS occurrences,
                AVG(latency_ms) AS avg_latency_ms,
                MODE(status) AS common_status
            FROM {prefix}provider_calls
            WHERE error_type IS NOT NULL
            GROUP BY 1, 2
            UNION ALL
            SELECT
                COALESCE(provider, model) AS provider,
                error_type,
                COUNT(*) AS occurrences,
                AVG(duration_ms) AS avg_latency_ms,
                MODE(status) AS common_status
            FROM {prefix}rerank_stages
            WHERE error_type IS NOT NULL
            GROUP BY 1, 2
            ORDER BY occurrences DESC, provider, error_type
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

    if _is_recent_events_question(q):
        sql = f"""
            SELECT
                recorded_at,
                query,
                intent,
                status_label,
                final_result_count,
                latency_tier,
                duration_s,
                rewrite_enabled,
                rewrite_model,
                rewrite_latency_s,
                rewrite_error,
                selected_providers,
                skipped_providers
            FROM {prefix}vw_run_summary
            ORDER BY recorded_at DESC
            LIMIT {limit}
        """
        return AnalyticsQueryPlan(sql=sql, view_prefix=prefix, rationale="events")

    raise ValueError(
        "Could not classify the analytics question. "
        "Supported topics: rerank, latency, run quality, provider, error, eval, recent events."
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
