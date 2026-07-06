"""Evals tab column layout."""

from __future__ import annotations

from prefab_ui.components import DataTableColumn


def build_evals_columns() -> list[DataTableColumn]:
    """Column layout for the Evals tab."""
    return [
        DataTableColumn(key="Eval Suite", header="Eval Suite Name", sortable=True),
        DataTableColumn(key="Tool Tested", header="Tool Being Tested", sortable=True),
        DataTableColumn(key="Test Cases", header="Test Cases Run", sortable=True),
        DataTableColumn(key="Passes", header="Passed ✓", sortable=True),
        DataTableColumn(key="Fails", header="Failed ✗", sortable=True),
        DataTableColumn(
            key="Avg Score (0–1)",
            header="Avg Score (0 = worst, 1 = best)",
            sortable=True,
        ),
    ]
