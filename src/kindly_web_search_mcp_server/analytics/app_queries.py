"""DuckDB query helpers for the analytics explorer UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .descriptions import _OBJECT_DESCRIPTIONS
from .views import ensure_local_views


# ── Formatting helpers ────────────────────────────────────────────────────────


def _fmt_int(val: Any) -> str:
    """Format a number as a comma-separated integer string, or '—' if None."""
    if val is None:
        return "—"
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_float(val: Any, decimals: int = 1) -> str:
    """Format a number as a fixed-decimal string, or '—' if None."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


# ── Low-level query helpers ───────────────────────────────────────────────────


def _query(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    """Run SQL and return a list of dicts. Returns [] on any error (safe fallback)."""
    try:
        result = con.execute(sql)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, default: Any = None) -> Any:
    """Run SQL and return the first column of the first row, or ``default``."""
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row else default
    except Exception:
        return default


# ── Main data fetcher ─────────────────────────────────────────────────────────


def _fetch_all(path: Path) -> dict[str, Any]:
    """Load all dashboard data from the DuckDB file in one connection.

    Calls ``ensure_local_views`` first (uses its own write connection) to make
    sure every view exists, then opens a read-only connection for all queries.
    """
    ensure_local_views(db_path=str(path))

    con = duckdb.connect(str(path), read_only=True)
    try:
        # ── KPI numbers ──────────────────────────────────────────────────────
        total_events = _scalar(con, "SELECT COUNT(*)::BIGINT FROM vw_events", 0)
        distinct_runs = _scalar(con, "SELECT COUNT(DISTINCT run_key)::BIGINT FROM vw_events", 0)
        hit_pct = _scalar(
            con,
            """
            SELECT ROUND(
                AVG(CASE WHEN cache_hit_text = 'true' THEN 100.0 ELSE 0.0 END), 1
            )
            FROM vw_cache_lookups
            """,
        )
        error_count = _scalar(con, "SELECT COUNT(*)::BIGINT FROM vw_error_events", 0)

        # ── Events tab ───────────────────────────────────────────────────────
        events = _query(
            con,
            """
            SELECT
                recorded_at::VARCHAR                            AS "Time",
                event_name                                      AS "Event Type",
                COALESCE(tool_name,  '—')                       AS "Tool",
                COALESCE(phase,      '—')                       AS "Phase",
                COALESCE(query,      '—')                       AS "Query",
                COALESCE(provider,   '—')                       AS "Provider",
                CASE
                    WHEN duration_ms IS NOT NULL
                    THEN ROUND(duration_ms, 0)::BIGINT::VARCHAR
                    ELSE '—'
                END                                             AS "Duration (ms)",
                COALESCE(cache_hit,  '—')                       AS "Cache Hit?"
            FROM vw_events
            ORDER BY recorded_at DESC
            LIMIT 200
            """,
        )

        # ── Cache tab ────────────────────────────────────────────────────────
        cache = _query(
            con,
            """
            SELECT
                cache_type                                                   AS "Cache Type",
                lookup_status                                                AS "Lookup Status",
                COUNT(*)                                                     AS "Total Lookups",
                SUM(CASE WHEN cache_hit_text = 'true'  THEN 1 ELSE 0 END)  AS "Hits (reused)",
                SUM(CASE WHEN cache_hit_text = 'false' THEN 1 ELSE 0 END)  AS "Misses (fetched fresh)",
                ROUND(AVG(duration_ms),      1)                             AS "Avg Duration (ms)",
                ROUND(AVG(similarity_score), 3)                             AS "Avg Similarity Score"
            FROM vw_cache_lookups
            GROUP BY cache_type, lookup_status
            ORDER BY "Total Lookups" DESC
            """,
        )

        # ── Providers tab ────────────────────────────────────────────────────
        providers = _query(
            con,
            """
            SELECT
                COALESCE(provider, '(unknown)')         AS "Provider",
                COUNT(*)                                AS "Result Rows",
                COUNT(DISTINCT run_key)                 AS "Search Runs",
                ROUND(AVG(score),          3)           AS "Avg Score",
                ROUND(AVG(provider_count), 1)           AS "Avg Provider Overlap"
            FROM vw_provider_results
            GROUP BY provider
            ORDER BY "Result Rows" DESC
            """,
        )

        # ── Errors tab ───────────────────────────────────────────────────────
        errors = _query(
            con,
            """
            SELECT
                event_name                              AS "Event Type",
                COALESCE(tool_name,  '—')               AS "Tool",
                COALESCE(provider,   '—')               AS "Provider",
                COALESCE(error_type, '(unknown)')       AS "Error Type",
                COUNT(*)                                AS "Occurrences"
            FROM vw_error_events
            GROUP BY event_name, tool_name, provider, error_type
            ORDER BY "Occurrences" DESC
            LIMIT 100
            """,
        )

        # ── Evals tab ────────────────────────────────────────────────────────
        evals = _query(
            con,
            """
            SELECT
                COALESCE(suite_name,  '—')              AS "Eval Suite",
                COALESCE(target_tool, '—')              AS "Tool Tested",
                COALESCE(cases,       0)                AS "Test Cases",
                COALESCE(passes,      0)                AS "Passes",
                COALESCE(fails,       0)                AS "Fails",
                ROUND(COALESCE(avg_score, 0), 3)        AS "Avg Score (0–1)"
            FROM vw_eval_provider_quality
            ORDER BY "Eval Suite", "Tool Tested"
            LIMIT 200
            """,
        )

        # ── Schema tab ───────────────────────────────────────────────────────
        # Collect all tables and views, then expand each into one row per column.
        schema_rows: list[dict[str, Any]] = []

        objects = _query(
            con,
            """
            SELECT table_name, 'table' AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            UNION ALL
            SELECT table_name, 'view'  AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'VIEW'
            ORDER BY kind DESC, table_name   -- tables first, then views
            """,
        )

        for obj in objects:
            obj_name: str = obj["table_name"]
            obj_kind: str = obj["kind"]
            description = _OBJECT_DESCRIPTIONS.get(obj_name, "")
            try:
                # PRAGMA table_info columns:
                #   0=cid, 1=name, 2=type, 3=notnull, 4=dflt_value, 5=pk
                cols = con.execute(f"PRAGMA table_info('{obj_name}')").fetchall()
                for col in cols:
                    schema_rows.append(
                        {
                            "Type": obj_kind,
                            "Table / View": obj_name,
                            "What it stores": description,
                            "Column": col[1],
                            "Data Type": col[2],
                        }
                    )
            except Exception:
                # Skip objects whose schema can't be introspected (e.g. broken views)
                pass

    finally:
        con.close()

    return {
        # KPI display strings (pre-formatted in Python so Rx just reads them as text)
        "kpi_events": _fmt_int(total_events),
        "kpi_runs": _fmt_int(distinct_runs),
        "kpi_cache": f"{_fmt_float(hit_pct)}%",
        "kpi_errors": _fmt_int(error_count),
        # Row data per tab
        "events_data": events,
        "cache_data": cache,
        "providers_data": providers,
        "errors_data": errors,
        "evals_data": evals,
        "schema_data": schema_rows,
    }
