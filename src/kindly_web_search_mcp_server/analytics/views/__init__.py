"""Analytics view bootstrap — dashboard, funnel-uplift, and fetch-observability views.

Module structure:
- :mod:`dashboard_sql` — 11 human-readable dashboard views over the canonical
  search/runs/candidates/judges tables.
- :mod:`funnel_sql` — 8 web-search funnel-uplift analytical views
  (provider contribution, candidate trajectory, branch contribution, etc.).
- :mod:`fetch_observability_sql` — fetch-tool stage, backend quality,
  follow-through, and freshness views.

The orchestration here owns DuckDB connection lifecycle, schema-installation
ordering, the process-wide lock, and the materialized summary rebuilds.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import duckdb

from .dashboard_sql import _build_dashboard_view_sql
from .fetch_observability_sql import _build_fetch_observability_view_sql
from .funnel_sql import _build_funnel_uplift_view_sql
from ..writers import (
    _db_path,
    ensure_store_schema,
    insert_table_freshness,
)
from ...settings import settings

_LOCK = threading.Lock()


def ensure_views(*, db_path: str | None = None) -> None:
    """Create or replace all analytics views against the local DuckDB store."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    if not path.exists():
        return
    # Schema installation acquires the same process lock. Perform it before
    # taking the view-creation lock to avoid a non-reentrant lock deadlock.
    ensure_store_schema(db_path=db_path)
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            # Dashboard views
            for statement in _build_dashboard_view_sql("main"):
                connection.execute(statement)
            # Funnel uplift views
            for statement in _build_funnel_uplift_view_sql("main"):
                connection.execute(statement)
            # Fetch observability views
            for statement in _build_fetch_observability_view_sql("main"):
                connection.execute(statement)
        finally:
            connection.close()
    _record_table_freshness(db_path=str(path))


def refresh_views(*, db_path: str | None = None) -> None:
    """Recreate all views (useful after schema migrations)."""
    ensure_views(db_path=db_path)


def _record_table_freshness(*, db_path: str) -> None:
    """Heartbeat the latest recorded timestamp and row count for core tables."""
    tables = (
        "content_operations",
        "content_fetches",
        "content_summaries",
        "content_stage_attempts",
        "content_fetch_items",
        "content_summary_rungs",
        "content_backend_health",
    )
    rows = []
    try:
        connection = duckdb.connect(db_path)
        try:
            for table_name in tables:
                count, latest = connection.execute(
                    f"SELECT COUNT(*), MAX(recorded_at) FROM {table_name}"
                ).fetchone()
                rows.append(
                    {
                        "table_name": table_name,
                        "checked_at": datetime.now(timezone.utc),
                        "max_recorded_at": latest,
                        "row_count": int(count or 0),
                    }
                )
        finally:
            connection.close()
    except Exception:
        return

    insert_table_freshness(rows, db_path=db_path)


def refresh_materialized_summaries(*, db_path: str | None = None) -> None:
    """Rebuild materialized summary tables from current fact data."""
    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)

    if not path.exists():
        return
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            connection.execute("""
                CREATE OR REPLACE TABLE summary_provider_discovery_daily AS
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    provider,
                    COUNT(*) AS total_calls,
                    COUNT(DISTINCT run_key) AS distinct_runs,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                    ROUND(AVG(latency_ms), 1) AS avg_latency_ms
                FROM provider_calls
                GROUP BY ALL
            """)
            connection.execute("""
                CREATE OR REPLACE TABLE summary_rewrite_value_daily AS
                SELECT
                    date_trunc('day', recorded_at)::DATE AS day,
                    variant_role,
                    COUNT(*) AS total_variants,
                    COUNT(*) FILTER (WHERE selected) AS selected_count,
                    COUNT(*) FILTER (WHERE executed) AS executed_count,
                    COUNT(*) FILTER (WHERE skip_reason IS NOT NULL) AS skipped_count
                FROM query_variants
                GROUP BY ALL
            """)
        finally:
            connection.close()


ensure_local_views = ensure_views


def build_analytics_view_sql(schema: str) -> list[str]:
    """Return SQL statements to create analytics views in a remote schema."""
    return [
        *_build_dashboard_view_sql(schema),
        *_build_funnel_uplift_view_sql(schema),
        *_build_fetch_observability_view_sql(schema),
    ]


__all__ = [
    "build_analytics_view_sql",
    "ensure_local_views",
    "ensure_views",
    "refresh_materialized_summaries",
    "refresh_views",
]
