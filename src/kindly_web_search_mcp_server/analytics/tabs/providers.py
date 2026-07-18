"""Providers tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_providers_columns() -> list[DataTableColumn]:
    """Column layout for the Providers tab (from vw_provider_performance)."""
    return [
        DataTableColumn(key="Provider", header="Search Provider", sortable=True),
        DataTableColumn(key="Total Calls", header="Total Calls", sortable=True),
        DataTableColumn(key="Success Count", header="Successes", sortable=True),
        DataTableColumn(key="Success Rate (%)", header="Success Rate", sortable=True),
        DataTableColumn(key="Avg Latency (ms)", header="Avg Latency (ms)", sortable=True),
        DataTableColumn(key="P95 Latency (ms)", header="P95 Latency (ms)", sortable=True),
        DataTableColumn(key="Total Results", header="Total Results", sortable=True),
        DataTableColumn(key="Error Count", header="Errors", sortable=True),
        DataTableColumn(key="Common Error", header="Most Common Error", sortable=True),
    ]
