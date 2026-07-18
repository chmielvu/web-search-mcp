"""Events tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_events_columns() -> list[DataTableColumn]:
    """Column layout for the Events tab (from vw_run_summary)."""
    return [
        DataTableColumn(key="Time", header="Time", sortable=True),
        DataTableColumn(key="Query", header="Search Query", sortable=True),
        DataTableColumn(key="Phase", header="Pipeline Phase", sortable=True),
        DataTableColumn(key="Status", header="Status", sortable=True),
        DataTableColumn(key="Duration (s)", header="Duration (s)", sortable=True),
        DataTableColumn(key="Providers", header="Providers", sortable=True),
    ]
