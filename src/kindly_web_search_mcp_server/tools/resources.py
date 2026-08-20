from __future__ import annotations

import json

from fastmcp.resources import ResourceContent, ResourceResult

from ._helpers import (
    _analytics_report_snapshot,
    _analytics_schema_snapshot,
    _public_settings_snapshot,
    _search_history_snapshot,
)
from .status import get_features_status, get_providers_status
from .workflow import get_workflow_doc


def get_providers_status_resource() -> ResourceResult:
    """Search provider configuration status."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=get_providers_status(),
                mime_type="text/markdown",
                meta={"title": "Search Provider Config Status"},
            )
        ]
    )


def get_features_status_resource() -> ResourceResult:
    """Feature flag status."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=get_features_status(),
                mime_type="text/markdown",
                meta={"title": "Feature Flag Status"},
            )
        ]
    )


def get_workflow_doc_resource() -> ResourceResult:
    """Web search workflow documentation."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=get_workflow_doc(),
                mime_type="text/markdown",
                meta={"title": "Web Search Workflow"},
            )
        ]
    )


def get_public_settings_resource() -> ResourceResult:
    """Public runtime settings with secrets removed."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(_public_settings_snapshot(), indent=2),
                mime_type="application/json",
                meta={"title": "Public Settings"},
            )
        ]
    )


def get_analytics_schema_resource() -> ResourceResult:
    """Analytics tables/views catalog for the local DuckDB observability store."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(_analytics_schema_snapshot(), indent=2),
                mime_type="application/json",
                meta={"title": "DuckDB Analytics Schema"},
            )
        ]
    )


def get_candidate_survival_resource() -> ResourceResult:
    """Default candidate-survival analytics report for the last 7 days."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(_analytics_report_snapshot("candidate-survival"), indent=2),
                mime_type="application/json",
                meta={"title": "Candidate Survival Report"},
            )
        ]
    )


def get_analytics_report_resource(report_name: str, days: int = 7) -> ResourceResult:
    """Parameterized analytics report resource using the deterministic report catalog."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(_analytics_report_snapshot(report_name, days=days), indent=2),
                mime_type="application/json",
                meta={"title": f"Report: {report_name} ({days}d)"},
            )
        ]
    )


def get_cache_stats_resource(cache_name: str | None = None) -> ResourceResult:
    """Entry counts for the server's local caches (query, page, transcript).

    Args:
        cache_name: Optional cache to report ("query", "page", "transcript").
            When omitted, all three caches are reported.
    """
    from ..cache import get_page_cache, get_query_cache, get_transcript_cache

    requested = {cache_name} if cache_name else {"query", "page", "transcript"}
    stats: dict[str, int] = {}
    if "query" in requested:
        stats["query"] = get_query_cache().entry_count()
    if "page" in requested:
        stats["page"] = get_page_cache().entry_count()
    if "transcript" in requested:
        stats["transcript"] = get_transcript_cache().entry_count()
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(stats, indent=2),
                mime_type="application/json",
                meta={"title": "Cache Entry Counts"},
            )
        ]
    )


def get_all_cache_stats_resource() -> ResourceResult:
    """Entry counts for all local caches (query, page, transcript)."""
    return get_cache_stats_resource(cache_name=None)


def get_search_history_resource(limit: int = 20) -> ResourceResult:
    """Recent search runs from the server's DuckDB analytics store."""
    return ResourceResult(
        contents=[
            ResourceContent(
                content=json.dumps(_search_history_snapshot(limit=limit), indent=2),
                mime_type="application/json",
                meta={"title": f"Search History (last {limit})"},
            )
        ]
    )
