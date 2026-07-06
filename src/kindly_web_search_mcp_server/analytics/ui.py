"""Prefab UI component assembly for the analytics explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefab_ui.actions import SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Button,
    Column,
    DataTable,
    DataTableColumn,
    Grid,
    Heading,
    Metric,
    Muted,
    Row,
    Separator,
    Text,
)
from prefab_ui.components.control_flow import If
from prefab_ui.rx import Rx

from .descriptions import _TAB_DESCRIPTIONS
from .tabs import (
    build_cache_columns,
    build_errors_columns,
    build_evals_columns,
    build_events_columns,
    build_providers_columns,
    build_schema_columns,
)

# ── Tab navigation order ─────────────────────────────────────────────────────

_TABS: list[tuple[str, str]] = [
    ("events", "Events"),
    ("cache", "Cache"),
    ("providers", "Providers"),
    ("errors", "Errors"),
    ("evals", "Evals"),
    ("schema", "Schema"),
]


# ── Prefab UI helpers ────────────────────────────────────────────────────────


def _tab_button(tab_id: str, label: str) -> None:
    """Render a tab-switching button. Sets 'active_tab' state on click."""
    Button(label, on_click=SetState("active_tab", tab_id))


def _tab_content(
    tab_id: str,
    columns: list[DataTableColumn],
    data_key: str,
) -> None:
    """Render one tab's description + DataTable, visible only when this tab is active."""
    with If(Rx(f"active_tab == '{tab_id}'")):
        with Column(gap=3):
            Text(
                _TAB_DESCRIPTIONS[tab_id],
                css_class="text-sm text-muted-foreground",
            )
            DataTable(
                columns=columns,
                rows=Rx(data_key),
                search=True,
            )


# ── Full UI builders ──────────────────────────────────────────────────────────


def build_empty_app(path: Path) -> PrefabApp:
    """Render the empty-state UI when the analytics DB file doesn't exist yet."""
    with PrefabApp() as empty_app:
        with Column(gap=4, css_class="p-8"):
            Heading("Analytics Explorer")
            Text(
                "No analytics data found yet.",
                css_class="text-lg text-muted-foreground",
            )
            Text(
                "Run some searches first — events are recorded automatically.",
                css_class="text-sm text-muted-foreground",
            )
            Text(
                f"Expected file: {path}",
                css_class="text-xs font-mono text-muted-foreground",
            )
    return empty_app


def build_app_ui(data: dict[str, Any]) -> PrefabApp:
    """Build the full analytics explorer UI from the fetched dashboard data."""
    with PrefabApp(state={**data, "active_tab": "events"}) as pa:
        with Column(gap=0):
            # Header ─────────────────────────────────────────────────────────
            with Column(gap=1, css_class="px-6 pt-6 pb-4"):
                Heading("Analytics Explorer")
                Muted(
                    "Live view of the web-search-mcp DuckDB event store — "
                    "click a tab to explore, use the search boxes to filter rows."
                )

            # KPI strip ───────────────────────────────────────────────────────
            # Four top-line numbers so you immediately know the overall health.
            with Grid(columns=4, gap=4, css_class="px-6 pb-4"):
                Metric(
                    label="Total Events",
                    value=Rx("kpi_events"),
                )
                Metric(
                    label="Search Runs",
                    value=Rx("kpi_runs"),
                )
                Metric(
                    label="Cache Hit Rate",
                    value=Rx("kpi_cache"),
                )
                Metric(
                    label="Errors Recorded",
                    value=Rx("kpi_errors"),
                )

            Separator()

            # Tab bar ─────────────────────────────────────────────────────────
            # Clicking a tab switches the content below instantly (client-side).
            with Row(gap=2, css_class="px-6 py-3 flex-wrap"):
                for tab_id, label in _TABS:
                    _tab_button(tab_id, label)

            Separator()

            # Tab content area ───────────────────────────────────────────────
            with Column(gap=3, css_class="px-6 py-4"):
                _tab_content("events", build_events_columns(), "events_data")
                _tab_content("cache", build_cache_columns(), "cache_data")
                _tab_content("providers", build_providers_columns(), "providers_data")
                _tab_content("errors", build_errors_columns(), "errors_data")
                _tab_content("evals", build_evals_columns(), "evals_data")
                _tab_content("schema", build_schema_columns(), "schema_data")

    return pa
