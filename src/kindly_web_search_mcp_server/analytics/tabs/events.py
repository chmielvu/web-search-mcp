"""Events tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_events_columns() -> list[DataTableColumn]:
    """Column layout for the Events tab."""
    return [
        DataTableColumn(key="Time", header="Time", sortable=True),
        DataTableColumn(key="Event Type", header="Event Type", sortable=True),
        DataTableColumn(key="Tool", header="Tool Called", sortable=True),
        DataTableColumn(key="Phase", header="Pipeline Phase", sortable=True),
        DataTableColumn(key="Query", header="Search Query", sortable=True),
        DataTableColumn(key="Provider", header="Search Provider", sortable=True),
        DataTableColumn(key="Duration (ms)", header="Duration (ms)", sortable=True),
        DataTableColumn(key="Cache Hit?", header="Served from Cache?", sortable=True),
    ]
