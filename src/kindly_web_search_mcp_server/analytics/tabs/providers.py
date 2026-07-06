"""Providers tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_providers_columns() -> list[DataTableColumn]:
    """Column layout for the Providers tab."""
    return [
        DataTableColumn(key="Provider", header="Search Provider", sortable=True),
        DataTableColumn(key="Result Rows", header="Result Rows Returned", sortable=True),
        DataTableColumn(key="Search Runs", header="Search Runs Used In", sortable=True),
        DataTableColumn(key="Avg Score", header="Avg Quality Score", sortable=True),
        DataTableColumn(
            key="Avg Provider Overlap",
            header="Avg Provider Overlap",
            sortable=True,
        ),
    ]
