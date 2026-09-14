"""Sync local DuckDB analytics into MotherDuck for Grafana querying."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ..settings import settings
from ..utils.paths import DEFAULT_EXTENSION_DIR
from .duckdb_store import ensure_store_schema
from .views import build_analytics_view_sql


DEFAULT_SCHEMA = "web_search_analytics"
DEFAULT_EXTENSION_DIR = DEFAULT_EXTENSION_DIR


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
    database = (value or os.environ.get("MOTHERDUCK_DATABASE") or "").strip()
    if not database:
        raise ValueError(
            "MotherDuck database is required. Set MOTHERDUCK_DATABASE or pass "
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
    before = connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0]  # type: ignore[index]
    predicate = " AND ".join(f"remote.{column} = local.{column}" for column in key_columns)
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
    after = connection.execute(f"SELECT count(*) FROM {target_table}").fetchone()[0]  # type: ignore[index]
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

    connection = duckdb.connect(str(source), config=_duckdb_config())  # type: ignore[arg-type]
    try:
        _load_motherduck(connection)
        connection.execute(f"ATTACH 'md:{database}' AS {_quote_ident(attach)}")
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {target}")
        # search_events table removed; keep analytics_event_raw only if already present.
        # Do not recreate or SELECT from the obsolete local table.
        source_rows = 0
        before = 0
        after = 0
        try:
            before = connection.execute(
                f"SELECT count(*) FROM {target}.analytics_event_raw"
            ).fetchone()[0]  # type: ignore[index]
            after = before
        except Exception:
            # Target table may not exist yet on a fresh MotherDuck schema.
            pass

    finally:
        connection.close()

    remote = duckdb.connect(f"md:{database}", config=_duckdb_config())  # type: ignore[arg-type]
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
