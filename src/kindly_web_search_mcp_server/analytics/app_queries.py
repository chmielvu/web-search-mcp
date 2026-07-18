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


def _pct_delta(cur: float, prev: float, *, inverse: bool = False) -> tuple[str, str]:
    """Return (delta_string, trend) for a KPI with prior-period comparison."""
    if not prev:
        return ("—", "flat")
    d = (cur - prev) / prev * 100.0
    if inverse:
        d = -d
    return (f"{d:+.1f}% vs prior 7d", "up" if d >= 0 else "down")


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
        # ── KPI numbers (current 7d window + prior 7d window for trend) ──
        total_events = _scalar(
            con,
            "SELECT COUNT(*) FROM search_runs WHERE recorded_at >= now() - INTERVAL '7 days'",
            0,
        )
        total_events_prev = _scalar(
            con,
            "SELECT COUNT(*) FROM search_runs WHERE recorded_at >= now() - INTERVAL '14 days' AND recorded_at < now() - INTERVAL '7 days'",
            0,
        )
        distinct_runs = _scalar(
            con,
            "SELECT COUNT(DISTINCT run_key) FROM search_runs WHERE recorded_at >= now() - INTERVAL '7 days'",
            0,
        )
        distinct_runs_prev = _scalar(
            con,
            "SELECT COUNT(DISTINCT run_key) FROM search_runs WHERE recorded_at >= now() - INTERVAL '14 days' AND recorded_at < now() - INTERVAL '7 days'",
            0,
        )
        avg_latency = _scalar(
            con,
            "SELECT ROUND(AVG(duration_ms)/1000.0, 2) FROM search_runs WHERE recorded_at >= now() - INTERVAL '7 days'",
            0.0,
        )
        avg_latency_prev = _scalar(
            con,
            "SELECT ROUND(AVG(duration_ms)/1000.0, 2) FROM search_runs WHERE recorded_at >= now() - INTERVAL '14 days' AND recorded_at < now() - INTERVAL '7 days'",
            0.0,
        )
        error_count = _scalar(
            con,
            "SELECT (SELECT COUNT(*) FROM provider_calls WHERE error_type IS NOT NULL) + (SELECT COUNT(*) FROM rerank_stages WHERE error_type IS NOT NULL)",
            0,
        )

        # ── Chart series ──
        daily_volume = _query(
            con,
            """
            SELECT date_trunc('day', recorded_at)::DATE::VARCHAR AS day,
                   COUNT(*) AS events
            FROM search_runs
            WHERE recorded_at >= now() - INTERVAL '7 days'
            GROUP BY 1 ORDER BY 1
            """,
        )
        provider_share = _query(
            con,
            "SELECT provider, total_calls AS calls FROM vw_provider_performance ORDER BY total_calls DESC LIMIT 10",
        )
        error_breakdown = _query(
            con,
            """
            SELECT COALESCE(error_type, 'unknown') AS error_type, COUNT(*) AS errors
            FROM (
                SELECT error_type FROM provider_calls WHERE error_type IS NOT NULL
                UNION ALL
                SELECT error_type FROM rerank_stages WHERE error_type IS NOT NULL
            ) t
            GROUP BY 1 ORDER BY errors DESC LIMIT 15
            """,
        )

        # ── Events tab — from vw_run_summary ──
        events_data = _query(
            con,
            """
            SELECT recorded_at::VARCHAR AS "Time",
                   query AS "Query",
                   COALESCE(intent, '—') AS "Phase",
                   status_label AS "Status",
                   ROUND(duration_s, 1)::VARCHAR AS "Duration (s)",
                   COALESCE(selected_providers, [])::VARCHAR AS "Providers"
            FROM vw_run_summary
            ORDER BY recorded_at DESC
            LIMIT 200
            """,
        )

        # ── Providers tab — from vw_provider_performance ──
        providers_data = _query(
            con,
            """
            SELECT provider AS "Provider",
                   total_calls AS "Total Calls",
                   success_count AS "Success Count",
                   success_rate_pct AS "Success Rate (%)",
                   ROUND(avg_latency_ms, 0)::VARCHAR AS "Avg Latency (ms)",
                   ROUND(p95_latency_ms, 0)::VARCHAR AS "P95 Latency (ms)",
                   total_results_returned AS "Total Results",
                   error_count AS "Error Count",
                   COALESCE(most_common_error, '—') AS "Common Error"
            FROM vw_provider_performance
            ORDER BY "Total Calls" DESC
            """,
        )

        # ── Errors tab — union of provider_calls + rerank_stages ──
        errors_data = _query(
            con,
            """
            SELECT 'provider_call' AS "Event Type",
                   COALESCE(provider, 'unknown') AS "Provider",
                   COALESCE(error_type, 'unknown') AS "Error Type",
                   COUNT(*) AS "Occurrences"
            FROM provider_calls WHERE error_type IS NOT NULL GROUP BY provider, error_type
            UNION ALL
            SELECT 'rerank_stage' AS "Event Type",
                   COALESCE(provider, COALESCE(model, 'unknown')) AS "Provider",
                   COALESCE(error_type, 'unknown') AS "Error Type",
                   COUNT(*) AS "Occurrences"
            FROM rerank_stages WHERE error_type IS NOT NULL GROUP BY provider, model, error_type
            ORDER BY "Occurrences" DESC
            LIMIT 100
            """,
        )

        # ── Evals tab — from vw_eval_provider_quality ──
        evals_data = _query(
            con,
            """
            SELECT COALESCE(suite_name, '—') AS "Eval Suite",
                   COALESCE(target_tool, '—') AS "Tool Tested",
                   COALESCE(cases, 0) AS "Test Cases",
                   COALESCE(passes, 0) AS "Passes",
                   COALESCE(fails, 0) AS "Fails",
                   ROUND(COALESCE(avg_score, 0.0), 3) AS "Avg Score (0-1)"
            FROM vw_eval_provider_quality
            ORDER BY "Eval Suite", "Tool Tested"
            LIMIT 200
            """,
        )

        # ── Schema tab ────────────────────────────────────────────────────────
        schema_rows: list[dict[str, Any]] = []
        objects = _query(
            con,
            """
            SELECT table_name, 'table' AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            UNION ALL
            SELECT table_name, 'view' AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'VIEW'
            ORDER BY kind DESC, table_name
            """,
        )
        for obj in objects:
            obj_name: str = obj["table_name"]
            obj_kind: str = obj["kind"]
            description = _OBJECT_DESCRIPTIONS.get(obj_name, "")
            try:
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
                pass

    finally:
        con.close()

    return {
        "kpi_events": _fmt_int(total_events),
        "kpi_events_delta": _pct_delta(total_events, total_events_prev)[0],
        "kpi_events_trend": _pct_delta(total_events, total_events_prev)[1],
        "kpi_runs": _fmt_int(distinct_runs),
        "kpi_runs_delta": _pct_delta(distinct_runs, distinct_runs_prev)[0],
        "kpi_runs_trend": _pct_delta(distinct_runs, distinct_runs_prev)[1],
        "kpi_latency": f"{_fmt_float(avg_latency)}s",
        "kpi_latency_delta": _pct_delta(avg_latency, avg_latency_prev)[0],
        "kpi_latency_trend": _pct_delta(avg_latency, avg_latency_prev, inverse=True)[1],
        "kpi_errors": _fmt_int(error_count),
        "kpi_errors_delta": "—",
        "kpi_errors_trend": "flat",
        "daily_volume": daily_volume,
        "provider_share": provider_share,
        "error_breakdown": error_breakdown,
        "events_data": events_data,
        "providers_data": providers_data,
        "errors_data": errors_data,
        "evals_data": evals_data,
        "schema_data": schema_rows,
    }
