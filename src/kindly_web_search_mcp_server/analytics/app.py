"""FastMCP App: interactive analytics explorer for the web-search-mcp DuckDB store.

This module exposes ``analytics_app`` — a FastMCPApp that renders a live,
tab-based dashboard directly inside the MCP conversation.  Every table has
plain-English column headers and a description so you can tell at a glance
what each row means.

Six tabs are available:

  Overview  — daily volume, provider share, and error breakdown charts
  Events    — the raw run log, newest first, from vw_run_summary
  Providers — which search backends fired and how they scored
  Errors    — failure/timeout counts by type and source
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
──────────────────────────────────
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

from fastmcp import FastMCP, FastMCPApp
from prefab_ui.app import PrefabApp

from ..settings import settings
from .app_queries import _fetch_all
from .descriptions import _OBJECT_DESCRIPTIONS  # noqa: F401
from .ui import build_app_ui, build_empty_app

# ── The FastMCPApp instance ──────────────────────────────────────────────────
# Import this and mount it on your server: FastMCP(..., providers=[analytics_app])

analytics_app = FastMCPApp("KindlyAnalytics")

# ── UI entry point ───────────────────────────────────────────────────────────


@analytics_app.ui()
def analytics_explorer() -> PrefabApp:
    """Open the Analytics Explorer.

    Browse all search events, cache statistics, provider performance, errors,
    eval results, and the full DuckDB schema — all in one interactive dashboard.
    """
    path = Path(settings.analytics_duckdb_path)

    # ── Graceful empty state ─────────────────────────────────────────────────
    if not path.exists():
        return build_empty_app(path)

    # ── Fetch all data upfront (DuckDB is fast) ──────────────────────────────
    data = _fetch_all(path)

    # ── Build the UI ─────────────────────────────────────────────────────────
    return build_app_ui(data)


# ── Dev server entrypoint ────────────────────────────────────────────────────
# Used by `fastmcp dev apps`.  Wraps the FastMCPApp in a FastMCP server so the
# CLI's run_async() dispatch (run.py:266) works.

mcp = FastMCP("web-search-mcp")
mcp.add_provider(analytics_app)
