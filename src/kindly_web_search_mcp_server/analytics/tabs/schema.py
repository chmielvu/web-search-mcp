"""Schema tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_schema_columns() -> list[DataTableColumn]:
    """Column layout for the Schema tab."""
    return [
        DataTableColumn(key="Type", header="Type (table / view)", sortable=True),
        DataTableColumn(key="Table / View", header="Table or View Name", sortable=True),
        DataTableColumn(
            key="What it stores",
            header="What it stores — plain English",
            sortable=False,
        ),
        DataTableColumn(key="Column", header="Column Name", sortable=True),
        DataTableColumn(key="Data Type", header="Data Type", sortable=True),
    ]
