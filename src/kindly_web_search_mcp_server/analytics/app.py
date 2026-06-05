"""FastMCP App: interactive analytics explorer for the web-search-mcp DuckDB store.

This module exposes ``analytics_app`` — a FastMCPApp that renders a live,
tab-based dashboard directly inside the MCP conversation.  Every table has
plain-English column headers and a description so you can tell at a glance
what each row means.

Six tabs are available:

  Events    — the raw event log, newest first (searches, rewrites, caches, …)
  Cache     — cache hit/miss breakdown with similarity scores
  Providers — which search backends fired and how they scored
  Errors    — failure/timeout counts by type and tool
  Evals     — automated eval suite pass/fail results
  Schema    — every table and view with all column definitions

──────────────────────────────────────────────────────────────────────────────

Mounting on the main server
───────────────────────────
In server.py (or wherever you build the FastMCP instance):

    from kindly_web_search_mcp_server.analytics.app import analytics_app

    mcp = FastMCP("web-search-mcp", providers=[..., analytics_app])

──────────────────────────────────────────────────────────────────────────────

Running standalone for development
───────────────────────────────────
    pip install "fastmcp[apps]"
    fastmcp dev apps src/kindly_web_search_mcp_server/analytics/app.py:mcp

Then open http://localhost:8080 and launch "analytics_explorer".

─────────────────────────────────────────────────────────────────────────────────

Requirements
────────────
    pip install "fastmcp[apps]"

prefab-ui changes frequently; pin it in your project:
    pip install "prefab-ui==<version shown by pip show prefab-ui>"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from fastmcp import FastMCP, FastMCPApp
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

# Ensure the package root is on sys.path so relative imports in sibling
# modules work when loaded via `fastmcp dev apps` (which imports this
# file as a one-off "server_module" without package context).
import sys as _sys
_src = Path(__file__).resolve().parent.parent.parent
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.analytics.views import ensure_local_views

# ── Plain-English tab descriptions ────────────────────────────────────────────
# These are shown at the top of each tab so you always know what you're looking at.

_TAB_DESCRIPTIONS: dict[str, str] = {
    "events": (
        "Every event the server fires — searches, rewrites, reranks, fetches, and cache lookups. "
        "Sorted newest first (up to 200 rows). "
        "Use the search box to filter by query text, tool name, or provider."
    ),
    "cache": (
        "'Hits' = a cached answer was reused (fast, no API call). "
        "'Misses' = fresh results had to be fetched from a search provider. "
        "Higher hit rates mean lower latency and lower API costs. "
        "'Avg Similarity' shows how close the cached query was to the new one."
    ),
    "providers": (
        "Which search backends were queried (Brave, SearXNG, Tavily, Gemini, …), "
        "how many result rows they contributed, and their average quality score. "
        "'Provider Overlap' counts how many backends agreed on the same result — "
        "higher overlap generally means higher confidence."
    ),
    "errors": (
        "Failures, timeouts, and exceptions, grouped by error type so you can spot recurring problems. "
        "A high 'Occurrences' count on one error type means something needs attention. "
        "Click column headers to sort."
    ),
    "evals": (
        "Automated benchmark results. Each row is one eval suite run against a specific tool. "
        "'Passes' = the server met the expected behavior for that test case. "
        "'Avg Score' is 0.0–1.0 where 1.0 is a perfect result."
    ),
    "schema": (
        "Every table and view that exists in the analytics DuckDB file. "
        "Each row is one column. Use 'Type' to distinguish raw tables from derived views. "
        "The 'What it stores' column explains the purpose of the table or view in plain English."
    ),
}

# ── Human-readable descriptions for every object in the DB ───────────────────

_OBJECT_DESCRIPTIONS: dict[str, str] = {
    # Raw tables
    "search_events": (
        "The raw event log. Every telemetry event the MCP server records is "
        "appended here as a row with a JSON payload."
    ),
    "eval_runs": (
        "Metadata for each eval suite run — which suite was used, who ran it, "
        "which dataset, and any notes."
    ),
    "eval_cases": (
        "Individual test cases. Each row is one query with an expected behavior "
        "and a link to the search run that was evaluated."
    ),
    "eval_observations": (
        "The verdict (pass/fail) and numeric quality score for each test case. "
        "One observation per case per eval run."
    ),
    "llm_quality_scores": (
        "Quality scores produced by an LLM acting as a judge. "
        "One row per score dimension per eval case."
    ),
    "eval_tool_calls": (
        "Every tool call made during an eval case execution, with its payload."
    ),
    "eval_candidate_sets": (
        "The set of candidate results recorded at each stage of the pipeline "
        "during an eval run."
    ),
    "eval_scores": (
        "Numeric metric scores per eval case — precision, recall, etc."
    ),
    "eval_judge_calls": (
        "Individual LLM judge invocations. Each row captures the judge model, "
        "the score it gave, and the full payload."
    ),
    "eval_failures": (
        "Eval cases that failed outright (e.g. the tool threw an exception). "
        "Each row has a failure code explaining what went wrong."
    ),
    "analytics_sync_state": (
        "Tracks MotherDuck sync state — when the last sync happened and "
        "how many rows were transferred."
    ),
    # Derived views
    "vw_events": (
        "Enriched version of the raw event log. Provider name and run key are "
        "coalesced from multiple possible fields so they're always filled in."
    ),
    "vw_quality_events": (
        "Events with their JSON result blobs extracted into named columns. "
        "Use this view to inspect individual search results, rewrites, and sources."
    ),
    "vw_run_timeline": (
        "One row per search run. Shows how many events fired at each stage "
        "(rewrite, rerank, fetch, answer) so you can trace what happened."
    ),
    "vw_provider_results": (
        "One row per search result returned by a provider. "
        "Includes title, URL, snippet, domain, and quality score."
    ),
    "vw_branch_candidates": (
        "When the orchestrator runs multiple query branches in parallel, "
        "each branch's results are recorded here — one row per result per branch."
    ),
    "vw_cache_lookups": (
        "Every time the cache was consulted. Shows hit/miss, how similar the "
        "cached query was (similarity score), its age, and its TTL."
    ),
    "vw_cache_stores": (
        "Every time a result was written into the cache. Shows how large the "
        "stored response was and whether metadata was included."
    ),
    "vw_middleware_events": (
        "Rate limiting and expensive-tool gate events. Shows which tool was "
        "rate-limited, which bucket it hit, and how long it waited."
    ),
    "vw_session_activity": (
        "Session lifecycle events — when sessions start, what tools they use, "
        "and when they expire."
    ),
    "vw_content_events": (
        "Web page content extraction events. Shows whether each page was "
        "successfully classified, what extraction method was used, and how large the result was."
    ),
    "vw_error_events": (
        "All error, timeout, and failure events. Includes error type, "
        "HTTP status code if applicable, and the tool/provider that failed."
    ),
    "vw_eval_case_timeline": (
        "Each eval test case joined to its search run timeline. "
        "Shows how many events fired during the evaluated run."
    ),
    "vw_eval_candidate_survival": (
        "How many candidate result URLs survived each stage of the pipeline "
        "for a given eval case. Useful for spotting where results get dropped."
    ),
    "vw_eval_provider_quality": (
        "Quality metrics for each provider, aggregated per eval suite. "
        "Shows passes, fails, and average score per tool."
    ),
    "vw_eval_fetch_quality": (
        "Fetch and content quality per eval case — which fetch backend was used, "
        "whether it succeeded, and how much text was extracted."
    ),
    "vw_eval_pass_rate": (
        "Overall pass rate per eval suite. Also shows how many cases cleared "
        "the judge's 0.7 quality score threshold."
    ),
}

# ── The FastMCPApp instance ───────────────────────────────────────────────────
# Import this and mount it on your server: FastMCP(..., providers=[analytics_app])

analytics_app = FastMCPApp("KindlyAnalytics")

# ── Tab navigation order ──────────────────────────────────────────────────────

_TABS: list[tuple[str, str]] = [
    ("events",    "Events"),
    ("cache",     "Cache"),
    ("providers", "Providers"),
    ("errors",    "Errors"),
    ("evals",     "Evals"),
    ("schema",    "Schema"),
]

# ── Internal helpers ──────────────────────────────────────────────────────────


def _fmt_int(val: Any) -> str:
    """Format a number as a comma-separated integer string, or '—' if None."""
    if val is None:
        return "—"
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_float(val: Any, decimals: int = 1) -> str:
    """Format a number as a fixed-decimal string, or '—' if None."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _query(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    """Run SQL and return a list of dicts. Returns [] on any error (safe fallback)."""
    try:
        result = con.execute(sql)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return []


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, default: Any = None) -> Any:
    """Run SQL and return the first column of the first row, or ``default``."""
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row else default
    except Exception:
        return default


# ── Main data fetcher ─────────────────────────────────────────────────────────


def _fetch_all(path: Path) -> dict:
    """Load all dashboard data from the DuckDB file in one connection.

    Calls ``ensure_local_views`` first (uses its own write connection) to make
    sure every view exists, then opens a read-only connection for all queries.
    """
    ensure_local_views(db_path=str(path))

    con = duckdb.connect(str(path), read_only=True)
    try:
        # ── KPI numbers ──────────────────────────────────────────────────────
        total_events = _scalar(
            con, "SELECT COUNT(*)::BIGINT FROM vw_events", 0
        )
        distinct_runs = _scalar(
            con, "SELECT COUNT(DISTINCT run_key)::BIGINT FROM vw_events", 0
        )
        hit_pct = _scalar(
            con,
            """
            SELECT ROUND(
                AVG(CASE WHEN cache_hit_text = 'true' THEN 100.0 ELSE 0.0 END), 1
            )
            FROM vw_cache_lookups
            """,
        )
        error_count = _scalar(
            con, "SELECT COUNT(*)::BIGINT FROM vw_error_events", 0
        )

        # ── Events tab ───────────────────────────────────────────────────────
        events = _query(
            con,
            """
            SELECT
                recorded_at::VARCHAR                            AS "Time",
                event_name                                      AS "Event Type",
                COALESCE(tool_name,  '—')                       AS "Tool",
                COALESCE(phase,      '—')                       AS "Phase",
                COALESCE(query,      '—')                       AS "Query",
                COALESCE(provider,   '—')                       AS "Provider",
                CASE
                    WHEN duration_ms IS NOT NULL
                    THEN ROUND(duration_ms, 0)::BIGINT::VARCHAR
                    ELSE '—'
                END                                             AS "Duration (ms)",
                COALESCE(cache_hit,  '—')                       AS "Cache Hit?"
            FROM vw_events
            ORDER BY recorded_at DESC
            LIMIT 200
            """,
        )

        # ── Cache tab ────────────────────────────────────────────────────────
        cache = _query(
            con,
            """
            SELECT
                cache_type                                                   AS "Cache Type",
                lookup_status                                                AS "Lookup Status",
                COUNT(*)                                                     AS "Total Lookups",
                SUM(CASE WHEN cache_hit_text = 'true'  THEN 1 ELSE 0 END)  AS "Hits (reused)",
                SUM(CASE WHEN cache_hit_text = 'false' THEN 1 ELSE 0 END)  AS "Misses (fetched fresh)",
                ROUND(AVG(duration_ms),      1)                             AS "Avg Duration (ms)",
                ROUND(AVG(similarity_score), 3)                             AS "Avg Similarity Score"
            FROM vw_cache_lookups
            GROUP BY cache_type, lookup_status
            ORDER BY "Total Lookups" DESC
            """,
        )

        # ── Providers tab ────────────────────────────────────────────────────
        providers = _query(
            con,
            """
            SELECT
                COALESCE(provider, '(unknown)')         AS "Provider",
                COUNT(*)                                AS "Result Rows",
                COUNT(DISTINCT run_key)                 AS "Search Runs",
                ROUND(AVG(score),          3)           AS "Avg Score",
                ROUND(AVG(provider_count), 1)           AS "Avg Provider Overlap"
            FROM vw_provider_results
            GROUP BY provider
            ORDER BY "Result Rows" DESC
            """,
        )

        # ── Errors tab ───────────────────────────────────────────────────────
        errors = _query(
            con,
            """
            SELECT
                event_name                              AS "Event Type",
                COALESCE(tool_name,  '—')               AS "Tool",
                COALESCE(provider,   '—')               AS "Provider",
                COALESCE(error_type, '(unknown)')       AS "Error Type",
                COUNT(*)                                AS "Occurrences"
            FROM vw_error_events
            GROUP BY event_name, tool_name, provider, error_type
            ORDER BY "Occurrences" DESC
            LIMIT 100
            """,
        )

        # ── Evals tab ────────────────────────────────────────────────────────
        evals = _query(
            con,
            """
            SELECT
                COALESCE(suite_name,  '—')              AS "Eval Suite",
                COALESCE(target_tool, '—')              AS "Tool Tested",
                COALESCE(cases,       0)                AS "Test Cases",
                COALESCE(passes,      0)                AS "Passes",
                COALESCE(fails,       0)                AS "Fails",
                ROUND(COALESCE(avg_score, 0), 3)        AS "Avg Score (0–1)"
            FROM vw_eval_provider_quality
            ORDER BY "Eval Suite", "Tool Tested"
            LIMIT 200
            """,
        )

        # ── Schema tab ───────────────────────────────────────────────────────
        # Collect all tables and views, then expand each into one row per column.
        schema_rows: list[dict] = []

        objects = _query(
            con,
            """
            SELECT table_name, 'table' AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            UNION ALL
            SELECT table_name, 'view'  AS kind
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'VIEW'
            ORDER BY kind DESC, table_name   -- tables first, then views
            """,
        )

        for obj in objects:
            obj_name: str = obj["table_name"]
            obj_kind: str = obj["kind"]
            description = _OBJECT_DESCRIPTIONS.get(obj_name, "")
            try:
                # PRAGMA table_info columns:
                #   0=cid, 1=name, 2=type, 3=notnull, 4=dflt_value, 5=pk
                cols = con.execute(f"PRAGMA table_info('{obj_name}')").fetchall()
                for col in cols:
                    schema_rows.append(
                        {
                            "Type":          obj_kind,
                            "Table / View":  obj_name,
                            "What it stores": description,
                            "Column":        col[1],
                            "Data Type":     col[2],
                        }
                    )
            except Exception:
                # Skip objects whose schema can't be introspected (e.g. broken views)
                pass

    finally:
        con.close()

    return {
        # KPI display strings (pre-formatted in Python so Rx just reads them as text)
        "kpi_events":     _fmt_int(total_events),
        "kpi_runs":       _fmt_int(distinct_runs),
        "kpi_cache":      f"{_fmt_float(hit_pct)}%",
        "kpi_errors":     _fmt_int(error_count),
        # Row data per tab
        "events_data":    events,
        "cache_data":     cache,
        "providers_data": providers,
        "errors_data":    errors,
        "evals_data":     evals,
        "schema_data":    schema_rows,
    }


# ── Prefab UI helpers ─────────────────────────────────────────────────────────


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


# ── UI entry point ────────────────────────────────────────────────────────────


@analytics_app.ui()
def analytics_explorer() -> PrefabApp:
    """Open the Analytics Explorer.

    Browse all search events, cache statistics, provider performance, errors,
    eval results, and the full DuckDB schema — all in one interactive dashboard.
    """
    path = Path(settings.analytics_duckdb_path)

    # ── Graceful empty state ──────────────────────────────────────────────────
    if not path.exists():
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

    # ── Fetch all data upfront (DuckDB is fast) ───────────────────────────────
    data = _fetch_all(path)

    # ── Build the UI ──────────────────────────────────────────────────────────
    with PrefabApp(state={**data, "active_tab": "events"}) as pa:
        with Column(gap=0):

            # Header ──────────────────────────────────────────────────────────
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

            # Tab content area ────────────────────────────────────────────────
            with Column(gap=3, css_class="px-6 py-4"):

                # ── Events ───────────────────────────────────────────────────
                _tab_content(
                    "events",
                    [
                        DataTableColumn(
                            key="Time",
                            header="Time",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Event Type",
                            header="Event Type",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Tool",
                            header="Tool Called",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Phase",
                            header="Pipeline Phase",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Query",
                            header="Search Query",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Provider",
                            header="Search Provider",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Duration (ms)",
                            header="Duration (ms)",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Cache Hit?",
                            header="Served from Cache?",
                            sortable=True,
                        ),
                    ],
                    "events_data",
                )

                # ── Cache ────────────────────────────────────────────────────
                _tab_content(
                    "cache",
                    [
                        DataTableColumn(
                            key="Cache Type",
                            header="Cache Type",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Lookup Status",
                            header="Lookup Status",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Total Lookups",
                            header="Total Lookups",
                            sortable=True,
                        ),
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
                        DataTableColumn(
                            key="Avg Duration (ms)",
                            header="Avg Duration (ms)",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Avg Similarity Score",
                            header="Avg Similarity Score",
                            sortable=True,
                        ),
                    ],
                    "cache_data",
                )

                # ── Providers ────────────────────────────────────────────────
                _tab_content(
                    "providers",
                    [
                        DataTableColumn(
                            key="Provider",
                            header="Search Provider",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Result Rows",
                            header="Result Rows Returned",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Search Runs",
                            header="Search Runs Used In",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Avg Score",
                            header="Avg Quality Score",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Avg Provider Overlap",
                            header="Avg Provider Overlap",
                            sortable=True,
                        ),
                    ],
                    "providers_data",
                )

                # ── Errors ───────────────────────────────────────────────────
                _tab_content(
                    "errors",
                    [
                        DataTableColumn(
                            key="Event Type",
                            header="Event Type",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Tool",
                            header="Tool That Failed",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Provider",
                            header="Provider Involved",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Error Type",
                            header="Error Type",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Occurrences",
                            header="Times Seen",
                            sortable=True,
                        ),
                    ],
                    "errors_data",
                )

                # ── Evals ────────────────────────────────────────────────────
                _tab_content(
                    "evals",
                    [
                        DataTableColumn(
                            key="Eval Suite",
                            header="Eval Suite Name",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Tool Tested",
                            header="Tool Being Tested",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Test Cases",
                            header="Test Cases Run",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Passes",
                            header="Passed ✓",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Fails",
                            header="Failed ✗",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Avg Score (0–1)",
                            header="Avg Score (0 = worst, 1 = best)",
                            sortable=True,
                        ),
                    ],
                    "evals_data",
                )

                # ── Schema ───────────────────────────────────────────────────
                _tab_content(
                    "schema",
                    [
                        DataTableColumn(
                            key="Type",
                            header="Type (table / view)",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Table / View",
                            header="Table or View Name",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="What it stores",
                            header="What it stores — plain English",
                            sortable=False,
                        ),
                        DataTableColumn(
                            key="Column",
                            header="Column Name",
                            sortable=True,
                        ),
                        DataTableColumn(
                            key="Data Type",
                            header="Data Type",
                            sortable=True,
                        ),
                    ],
                    "schema_data",
                )

    return pa


# ── Dev server entrypoint ──────────────────────────────────────────────────────
# Used by `fastmcp dev apps`.  Wraps the FastMCPApp in a FastMCP server so the
# CLI's run_async() dispatch (run.py:266) works.

mcp = FastMCP("web-search-mcp")
mcp.add_provider(analytics_app)
