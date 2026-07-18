"""Prefab UI component assembly for the analytics explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Card,
    CardContent,
    Column,
    DataTable,
    Grid,
    Heading,
    Metric,
    Muted,
    Separator,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.components.charts import AreaChart, BarChart, ChartSeries, PieChart
from prefab_ui.rx import Rx

from .descriptions import _TAB_DESCRIPTIONS
from .tabs import (
    build_errors_columns,
    build_evals_columns,
    build_events_columns,
    build_providers_columns,
    build_schema_columns,
)


# ── Full UI builders ──────────────────────────────────────────────────────────


def build_empty_app(path: Path) -> PrefabApp:
    """Render the empty-state UI when the analytics DB file doesn't exist yet."""
    with PrefabApp(state={"db_path": str(path)}) as empty_app:
        with Column(gap=4, css_class="p-6"):
            Heading("Analytics Explorer")
            with Column(gap=2):
                Muted("No analytics data yet.")
                Text(f"Expected DB at: {path}")
                Muted(
                    "Run some searches first, then the DuckDB file will be created "
                    "and populated automatically."
                )
    return empty_app


def build_app_ui(data: dict[str, Any]) -> PrefabApp:
    """Build the full analytics explorer UI from the fetched dashboard data."""
    with PrefabApp(state={**data}) as pa:
        with Column(gap=0):
            with Column(gap=1, css_class="px-6 pt-6 pb-4"):
                Heading("Analytics Explorer")
                Muted(
                    "Live view of the web-search-mcp DuckDB store — KPIs with 7-day trend, "
                    "charts, and searchable tables."
                )
            # KPI strip (responsive, with trend deltas)
            with Grid(columns={"default": 1, "md": 2, "lg": 4}, gap=4, css_class="px-6 pb-4"):
                with Card():
                    with CardContent():
                        Metric(
                            label="Total Events",
                            value=Rx("kpi_events"),
                            delta=Rx("kpi_events_delta"),
                            trend=Rx("kpi_events_trend"),
                        )
                with Card():
                    with CardContent():
                        Metric(
                            label="Search Runs",
                            value=Rx("kpi_runs"),
                            delta=Rx("kpi_runs_delta"),
                            trend=Rx("kpi_runs_trend"),
                        )
                with Card():
                    with CardContent():
                        Metric(
                            label="Avg Latency",
                            value=Rx("kpi_latency"),
                            delta=Rx("kpi_latency_delta"),
                            trend=Rx("kpi_latency_trend"),
                        )
                with Card():
                    with CardContent():
                        Metric(
                            label="Errors",
                            value=Rx("kpi_errors"),
                            delta=Rx("kpi_errors_delta"),
                            trend=Rx("kpi_errors_trend"),
                        )
            Separator()
            with Tabs(css_class="px-6 py-4"):
                with Tab("Overview"):
                    Text("Trends over the last 7 days.")
                    with Grid(columns=2, gap=6):
                        with Column(gap=2):
                            Heading("Daily Volume", level=3)
                            AreaChart(
                                data=Rx("daily_volume"),
                                series=[ChartSeries(data_key="events", label="Events")],
                                x_axis="day",
                                curve="smooth",
                                show_legend=True,
                                height=220,
                            )
                        with Column(gap=2):
                            Heading("Provider Share", level=3)
                            PieChart(
                                data=Rx("provider_share"),
                                data_key="calls",
                                name_key="provider",
                                show_legend=True,
                                inner_radius=60,
                                height=220,
                            )
                    Separator(css_class="my-4")
                    with Column(gap=2):
                        Heading("Errors by Type", level=3)
                        BarChart(
                            data=Rx("error_breakdown"),
                            series=[ChartSeries(data_key="errors", label="Errors")],
                            x_axis="error_type",
                            show_legend=True,
                            height=220,
                        )
                with Tab("Events"):
                    Text(_TAB_DESCRIPTIONS.get("events", ""))
                    DataTable(
                        columns=build_events_columns(),
                        rows=Rx("events_data"),
                        search=True,
                        paginated=True,
                        page_size=25,
                    )
                with Tab("Providers"):
                    Text(_TAB_DESCRIPTIONS.get("providers", ""))
                    DataTable(
                        columns=build_providers_columns(),
                        rows=Rx("providers_data"),
                        search=True,
                        paginated=True,
                        page_size=25,
                    )
                with Tab("Errors"):
                    Text(_TAB_DESCRIPTIONS.get("errors", ""))
                    DataTable(
                        columns=build_errors_columns(),
                        rows=Rx("errors_data"),
                        search=True,
                        paginated=True,
                        page_size=25,
                    )
                with Tab("Evals"):
                    Text(_TAB_DESCRIPTIONS.get("evals", ""))
                    DataTable(
                        columns=build_evals_columns(),
                        rows=Rx("evals_data"),
                        search=True,
                        paginated=True,
                        page_size=25,
                    )
                with Tab("Schema"):
                    Text(_TAB_DESCRIPTIONS.get("schema", ""))
                    DataTable(
                        columns=build_schema_columns(),
                        rows=Rx("schema_data"),
                        search=True,
                        paginated=True,
                        page_size=25,
                    )
    return pa
