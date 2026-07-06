"""Errors tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_errors_columns() -> list[DataTableColumn]:
    """Column layout for the Errors tab."""
    return [
        DataTableColumn(key="Event Type", header="Event Type", sortable=True),
        DataTableColumn(key="Tool", header="Tool That Failed", sortable=True),
        DataTableColumn(key="Provider", header="Provider Involved", sortable=True),
        DataTableColumn(key="Error Type", header="Error Type", sortable=True),
        DataTableColumn(key="Occurrences", header="Times Seen", sortable=True),
    ]
