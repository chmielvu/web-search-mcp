from __future__ import annotations

from ._helpers import (
    _analytics_report_snapshot,
    _analytics_schema_snapshot,
    _cache_stats_snapshot,
    _public_settings_snapshot,
)


def get_public_settings_resource() -> dict[str, object]:
    """Public runtime settings and configured capabilities, with secrets removed."""
    return _public_settings_snapshot()


def get_cache_stats_resource() -> dict[str, object]:
    """Public cache topology and configured limits for exact/page layers."""
    return _cache_stats_snapshot()


def get_analytics_schema_resource() -> dict[str, object]:
    """Analytics tables/views catalog for the local DuckDB observability store."""
    return _analytics_schema_snapshot()


def get_candidate_survival_resource() -> dict[str, object]:
    """Default candidate-survival analytics report for the last 7 days."""
    return _analytics_report_snapshot("candidate-survival")


def get_cache_hit_rates_resource() -> dict[str, object]:
    """Default cache-hit-rate analytics report for the last 7 days."""
    return _analytics_report_snapshot("cache-hit-rates")


def get_analytics_report_resource(
    report_name: str,
    days: int = 7,
) -> dict[str, object]:
    """Parameterized analytics report resource using the deterministic report catalog."""
    return _analytics_report_snapshot(report_name, days=days)
