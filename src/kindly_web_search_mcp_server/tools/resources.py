from __future__ import annotations

import json

from fastmcp.resources import ResourceContent, ResourceResult

from ._helpers import (
    _analytics_report_snapshot,
    _analytics_schema_snapshot,
    _public_settings_snapshot,
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
