"""Sync local DuckDB analytics into MotherDuck for Grafana querying."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ..settings import settings
from .duckdb_store import ensure_store_schema
from .evals import build_eval_table_sql, ensure_eval_tables
from .views import build_analytics_view_sql


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
        raise ValueError("MOTHERDUCK_TOKEN is required to sync analytics to MotherDuck.")
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
    return {"extension_directory": extension_dir}


def _sync_append_only(
    connection: duckdb.DuckDBPyConnection,
    *,
    source_table: str,
    target_table: str,
    key_columns: list[str],
) -> int:
    before = connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0]
    predicate = " AND ".join(
        f"remote.{column} = local.{column}" for column in key_columns
    )
    connection.execute(
        f"""
        INSERT INTO {target_table} BY NAME
        SELECT local.*
        FROM {source_table} AS local
        WHERE NOT EXISTS (
            SELECT 1
            FROM {target_table} AS remote
            WHERE {predicate}
        )
        """
    )
    after = connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0]
    return int(after - before)


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
        f"""
        CREATE OR REPLACE TABLE {target}.eval_quality_daily AS
        WITH llm_scores AS (
            SELECT
                eval_case_id,
                AVG(score_value) AS avg_llm_score,
                COUNT(*) AS score_rows
            FROM {target}.llm_quality_scores
            GROUP BY 1
        ),
        case_scores AS (
            SELECT
                eval_case_id,
                AVG(score) AS avg_observation_score,
                COUNT(*) FILTER (WHERE verdict = 'pass') AS passes,
                COUNT(*) FILTER (WHERE verdict = 'fail') AS fails
            FROM {target}.eval_observations
            GROUP BY 1
        )
        SELECT
            date_trunc('day', r.created_at) AS day,
            r.suite_name,
            c.target_tool,
            COUNT(DISTINCT c.eval_case_id) AS cases,
            COUNT(DISTINCT r.eval_run_id) AS runs,
            SUM(COALESCE(o.passes, 0)) AS passes,
            SUM(COALESCE(o.fails, 0)) AS fails,
            AVG(o.avg_observation_score) AS avg_score,
            SUM(COALESCE(q.score_rows, 0)) AS llm_score_rows,
            AVG(q.avg_llm_score) AS avg_llm_score
        FROM {target}.eval_runs AS r
        LEFT JOIN {target}.eval_cases AS c
          ON c.eval_run_id = r.eval_run_id
        LEFT JOIN case_scores AS o
          ON o.eval_case_id = c.eval_case_id
        LEFT JOIN llm_scores AS q
          ON q.eval_case_id = c.eval_case_id
        GROUP BY 1, 2, 3
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
    ensure_eval_tables(db_path=str(source))

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
            f"CREATE TABLE IF NOT EXISTS {target}.analytics_event_raw AS SELECT * FROM search_events WHERE false"
        )
        for statement in build_eval_table_sql(target):
            connection.execute(statement)

        source_rows = connection.execute(
            "SELECT count(*) FROM search_events"
        ).fetchone()[0]
        last_event_id = connection.execute(
            "SELECT max(event_id) FROM search_events"
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

        for source_table, key_columns in (
            ("eval_runs", ["eval_run_id"]),
            ("eval_cases", ["eval_case_id"]),
            ("eval_observations", ["eval_observation_id"]),
            ("llm_quality_scores", ["score_id"]),
        ):
            _sync_append_only(
                connection,
                source_table=source_table,
                target_table=f"{target}.{source_table}",
                key_columns=key_columns,
            )

        sync_payload = json.dumps(
            {
                "database": database,
                "schema": schema,
                "source_rows": int(source_rows),
                "target_rows": int(after),
                "inserted_rows": int(after - before),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        connection.execute(f"DELETE FROM {target}.analytics_sync_state WHERE target_name = ?", [schema])
        connection.execute(
            f"""
            INSERT INTO {target}.analytics_sync_state
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
            """,
            [schema, int(source_rows), int(after), last_event_id, sync_payload],
        )
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
