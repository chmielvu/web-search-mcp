"""Cache tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_cache_columns() -> list[DataTableColumn]:
    """Column layout for the Cache tab."""
    return [
        DataTableColumn(key="Cache Type", header="Cache Type", sortable=True),
        DataTableColumn(key="Lookup Status", header="Lookup Status", sortable=True),
        DataTableColumn(key="Total Lookups", header="Total Lookups", sortable=True),
        DataTableColumn(
            key="Hits (reused)",
            header="Hits — cache reused ✓",
            sortable=True,
        ),
        DataTableColumn(
            key="Misses (fetched fresh)",
            header="Misses — fetched fresh ✗",
            sortable=True,
        ),
        DataTableColumn(key="Avg Duration (ms)", header="Avg Duration (ms)", sortable=True),
        DataTableColumn(
            key="Avg Similarity Score",
            header="Avg Similarity Score",
            sortable=True,
        ),
    ]
