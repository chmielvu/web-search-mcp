"""Fetch-tool observability tables (new tables only, no migrations).

Mirrors ``duckdb_data/reports/1109analysis/fetch_observability_schema.sql``.
Producers: ``content/fetch_pipeline.py`` (stage attempts) and
``utils/observability.py`` (fetch items, summary rungs). The
``content_backend_health`` table has a schema and writer but no live
producer.
"""

from __future__ import annotations

import duckdb

from .table_names import (
    _ATF_TABLE_NAME,
    _CBH_TABLE_NAME,
    _CFI_TABLE_NAME,
    _CSA_TABLE_NAME,
    _CSRUG_TABLE_NAME,
)


def _create(connection: duckdb.DuckDBPyConnection, table_name: str, ddl_body: str) -> None:
    connection.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (\n{ddl_body}\n)")


def _ensure_content_stage_attempts(connection: duckdb.DuckDBPyConnection) -> None:
    _create(
        connection,
        _CSA_TABLE_NAME,
        """
        attempt_id          VARCHAR PRIMARY KEY,
        terminal_event_id   VARCHAR NOT NULL,
        tool_call_id        VARCHAR NOT NULL,
        item_index          INTEGER NOT NULL,
        normalized_url      VARCHAR NOT NULL,
        stage               VARCHAR NOT NULL,
        stage_order         INTEGER NOT NULL,
        outcome             VARCHAR NOT NULL CHECK (outcome IN ('success', 'partial', 'blocked', 'unsupported', 'error', 'skipped')),
        error_code          VARCHAR,
        error_category      VARCHAR CHECK (error_category IN ('validation', 'auth', 'rate_limit', 'upstream', 'blocked', 'timeout', 'internal')),
        retryable           BOOLEAN,
        http_status         INTEGER,
        latency_ms          DOUBLE,
        attempt_count       INTEGER NOT NULL DEFAULT 1,
        bytes_downloaded    BIGINT,
        chars_kept          INTEGER,
        quality_score       DOUBLE,
        skipped_reason      VARCHAR CHECK (skipped_reason IN ('budget_too_small', 'client_unconfigured', 'binary_target', 'parser_no_match', 'not_attempted')),
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csa_tool_call ON content_stage_attempts(tool_call_id, item_index)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csa_stage_outcome_time ON content_stage_attempts(stage, outcome, recorded_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csa_url_time ON content_stage_attempts(normalized_url, recorded_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csa_error ON content_stage_attempts(error_category, recorded_at)"
    )


def _ensure_content_fetch_items(connection: duckdb.DuckDBPyConnection) -> None:
    _create(
        connection,
        _CFI_TABLE_NAME,
        """
        terminal_event_id   VARCHAR NOT NULL,
        tool_call_id        VARCHAR NOT NULL,
        item_index          INTEGER NOT NULL,
        error_code          VARCHAR,
        error_category      VARCHAR CHECK (error_category IN ('validation', 'auth', 'rate_limit', 'upstream', 'blocked', 'timeout', 'internal')),
        error_retryable     BOOLEAN,
        error_http_status   INTEGER,
        error_message       VARCHAR,
        quality_score       DOUBLE,
        title               VARCHAR,
        bytes_downloaded    BIGINT,
        redirect_count      INTEGER,
        stage_count         INTEGER,
        stage_path          VARCHAR,
        diagnostics_json    VARCHAR,
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (terminal_event_id, tool_call_id, item_index)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cfi_error ON content_fetch_items(error_category, recorded_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cfi_backend_time ON content_fetch_items(tool_call_id, recorded_at)"
    )


def _ensure_content_summary_rungs(connection: duckdb.DuckDBPyConnection) -> None:
    _create(
        connection,
        _CSRUG_TABLE_NAME,
        """
        terminal_event_id   VARCHAR NOT NULL,
        tool_call_id        VARCHAR NOT NULL,
        item_index          INTEGER NOT NULL DEFAULT 0,
        rung                VARCHAR NOT NULL,
        rung_order          INTEGER NOT NULL,
        provider            VARCHAR CHECK (provider IN ('google', 'gemma')),
        model_used          VARCHAR,
        outcome             VARCHAR NOT NULL CHECK (outcome IN ('success', 'error', 'skipped')),
        error_type          VARCHAR,
        input_tokens        INTEGER,
        output_tokens       INTEGER,
        latency_ms          DOUBLE,
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (terminal_event_id, tool_call_id, item_index, rung_order)
        """,
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_csr_call ON content_summary_rungs(tool_call_id, rung_order)"
    )


def _ensure_content_backend_health(connection: duckdb.DuckDBPyConnection) -> None:
    _create(
        connection,
        _CBH_TABLE_NAME,
        """
        backend               VARCHAR NOT NULL,
        checked_at            TIMESTAMPTZ NOT NULL,
        healthy               BOOLEAN NOT NULL,
        check_latency_ms      DOUBLE,
        consecutive_failures  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (backend, checked_at)
        """,
    )


def _ensure_analytics_table_freshness(connection: duckdb.DuckDBPyConnection) -> None:
    _create(
        connection,
        _ATF_TABLE_NAME,
        """
        table_name       VARCHAR NOT NULL,
        checked_at       TIMESTAMPTZ NOT NULL,
        max_recorded_at  TIMESTAMPTZ,
        row_count        BIGINT NOT NULL,
        PRIMARY KEY (table_name, checked_at)
        """,
    )
