"""Errors tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_errors_columns() -> list[DataTableColumn]:
    """Column layout for the Errors tab (union of provider_calls + rerank_stages)."""
    return [
        DataTableColumn(key="Event Type", header="Source", sortable=True),
        DataTableColumn(key="Provider", header="Provider / Model", sortable=True),
        DataTableColumn(key="Error Type", header="Error Type", sortable=True),
        DataTableColumn(key="Occurrences", header="Occurrences", sortable=True),
    ]
