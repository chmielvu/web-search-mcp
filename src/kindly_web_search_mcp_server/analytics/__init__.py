"""Analytics sinks for offline tuning and inspection."""

from .duckdb_store import ensure_store_schema
from .evals import build_eval_table_sql, build_eval_view_sql, ensure_eval_tables
from .reports import available_reports, run_report
from .motherduck_sync import sync_once
from .queries import build_analytics_query_plan, run_analytics_query
from .views import ensure_local_views

__all__ = [
    "build_analytics_query_plan",
    "build_eval_table_sql",
    "build_eval_view_sql",
    "available_reports",
    "ensure_local_views",
    "ensure_eval_tables",
    "ensure_store_schema",
    "run_report",
    "run_analytics_query",
    "sync_once",
]
