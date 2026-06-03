"""Local DuckDB and MotherDuck evaluation tables and join views."""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..settings import settings


def build_eval_table_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_runs (
            eval_run_id VARCHAR,
            created_at TIMESTAMP,
            suite_name VARCHAR,
            evaluator VARCHAR,
            dataset_name VARCHAR,
            prompt_version VARCHAR,
            notes_json VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_cases (
            eval_case_id VARCHAR,
            eval_run_id VARCHAR,
            recorded_at TIMESTAMP,
            target_tool VARCHAR,
            query VARCHAR,
            expected_behavior VARCHAR,
            expected_output_json VARCHAR,
            labels_json VARCHAR,
            trace_id VARCHAR,
            run_key VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_observations (
            eval_observation_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            event_name VARCHAR,
            run_key VARCHAR,
            score DOUBLE,
            verdict VARCHAR,
            notes_json VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.llm_quality_scores (
            score_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            score_name VARCHAR,
            score_value DOUBLE,
            model_name VARCHAR,
            explanation VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_tool_calls (
            tool_call_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            tool_name VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_candidate_sets (
            candidate_set_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            set_name VARCHAR,
            candidates_json VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_scores (
            score_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            metric_name VARCHAR,
            score_value DOUBLE,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_judge_calls (
            judge_call_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            judge_model VARCHAR,
            score_value DOUBLE,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.eval_failures (
            failure_id VARCHAR,
            eval_run_id VARCHAR,
            eval_case_id VARCHAR,
            recorded_at TIMESTAMP,
            run_key VARCHAR,
            failure_code VARCHAR,
            payload_json VARCHAR
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {target}.analytics_sync_state (
            target_name VARCHAR,
            last_synced_at TIMESTAMP,
            source_rows BIGINT,
            target_rows BIGINT,
            last_event_id VARCHAR,
            payload_json VARCHAR
        )
        """,
    ]


def build_eval_view_sql(target: str) -> list[str]:
    return [
        f"""
        CREATE OR REPLACE VIEW {target}.vw_eval_case_timeline AS
        SELECT
            c.eval_case_id,
            c.eval_run_id,
            c.recorded_at AS case_recorded_at,
            c.target_tool,
            c.query,
            c.expected_behavior,
            c.expected_output_json,
            c.labels_json,
            c.run_key,
            t.first_seen_at,
            t.last_seen_at,
            t.event_count,
            t.rewrite_events,
            t.rerank_events,
            t.fetch_events,
            t.answer_events
        FROM {target}.eval_cases AS c
        LEFT JOIN {target}.vw_run_timeline AS t
          ON t.run_key = c.run_key
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_eval_candidate_survival AS
        SELECT
            c.eval_run_id,
            c.eval_case_id,
            s.stage,
            COUNT(*) AS rows,
            COUNT(DISTINCT s.run_key) AS runs,
            COUNT(DISTINCT s.url) AS unique_urls
        FROM {target}.eval_cases AS c
        LEFT JOIN {target}.vw_candidate_survival AS s
          ON s.run_key = c.run_key
        GROUP BY 1, 2, 3
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_eval_provider_quality AS
        SELECT
            r.eval_run_id,
            r.suite_name,
            c.target_tool,
            COUNT(*) AS cases,
            COUNT(*) FILTER (WHERE o.verdict = 'pass') AS passes,
            COUNT(*) FILTER (WHERE o.verdict = 'fail') AS fails,
            AVG(o.score) AS avg_score
        FROM {target}.eval_runs AS r
        LEFT JOIN {target}.eval_cases AS c
          ON c.eval_run_id = r.eval_run_id
        LEFT JOIN {target}.eval_observations AS o
          ON o.eval_case_id = c.eval_case_id
        GROUP BY 1, 2, 3
        """,
        f"""
        CREATE OR REPLACE VIEW {target}.vw_eval_fetch_quality AS
        SELECT
            c.eval_run_id,
            c.eval_case_id,
            coalesce(f.fetch_backend, 'unknown') AS fetch_backend,
            coalesce(f.status, 'unknown') AS status,
            COUNT(*) AS fetch_events,
            AVG(f.word_count) AS avg_word_count,
            AVG(f.page_char_count) AS avg_page_char_count
        FROM {target}.eval_cases AS c
        LEFT JOIN {target}.vw_fetch_events AS f
          ON f.run_key = c.run_key
        GROUP BY 1, 2, 3, 4
        """,
    ]


def ensure_eval_tables(*, db_path: str | None = None) -> None:
    path = Path(db_path or settings.analytics_duckdb_path)
    if not path.exists():
        raise FileNotFoundError(f"Analytics DuckDB file does not exist: {path}")

    connection = duckdb.connect(str(path))
    try:
        for statement in build_eval_table_sql("main"):
            connection.execute(statement)
    finally:
        connection.close()
