"""Observability schema — stripped to provider_health_transitions only.

The 5 other observability tables (web_search_tool_calls, web_search_response_results,
branch_attempts, branch_candidates, pipeline_heartbeats) are dropped in the
clean-cutover redesign.  Their DDL now lives in ``writers/schema.py`` as part of
the unified 9-table schema.  This module retains only the
``provider_health_transitions`` table and a thin ``ensure_pipeline_observability_tables``
shim for backward-compat callers (e.g. ``utils/observability.py``).
"""

from __future__ import annotations

from .writers.schema import _ensure_provider_health_transitions

_PROVIDER_HEALTH_TABLE = "provider_health_transitions"


def ensure_pipeline_observability_tables(*, db_path: str | None = None) -> None:
    """Create the provider_health_transitions table if absent.

    The other 5 observability tables are removed in the clean cutover.
    """
    from .writers.connection import _LOCK, _db_path
    import duckdb

    from ..settings import settings

    if not settings.analytics_enabled:
        return
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        connection = duckdb.connect(str(path))
        try:
            _ensure_provider_health_transitions(connection)
        finally:
            connection.close()
