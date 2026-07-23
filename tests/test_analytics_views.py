from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestAnalyticsViews:
    def _append_event(self, event_name: str, payload: dict[str, object], db_path: Path) -> None:
        from kindly_web_search_mcp_server.analytics.duckdb_store import append_event

        append_event(event_name, payload, db_path=str(db_path))

    def _ensure_local_views(self, db_path: Path) -> None:
        from kindly_web_search_mcp_server.analytics.views import ensure_local_views

        ensure_local_views(db_path=str(db_path))
