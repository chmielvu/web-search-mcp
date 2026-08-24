from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import (
    insert_provider_calls,
)
from kindly_web_search_mcp_server.search.contracts import BranchRole, QueryBranch
from kindly_web_search_mcp_server.search.providers.base import ProviderRequestMetadata
from kindly_web_search_mcp_server.search.retrieval import _record_provider_result


def _branch() -> QueryBranch:
    return QueryBranch(
        role=BranchRole.SEMANTIC_EXA,
        query="repo:acme/demo needle",
        provider_names=("gitlab",),
        max_results=5,
    )


def test_provider_call_persists_planner_and_adapter_queries(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    _record_provider_result(
        branch=_branch(),
        branch_index=0,
        name="gitlab",
        value=[],
        latency_ms=12.5,
        rows=OrderedDict(),
        warnings_by_name={},
        provider_calls=calls,
        provider_ranked_results_list=[],
        request_query="needle",
        metadata=ProviderRequestMetadata(
            provider="gitlab",
            endpoint="https://gitlab.com/api/v4/search?scope=blobs&search=needle",
            http_status=200,
            result_class="empty",
            auth_mode="anonymous",
            response_meta={"scope": "blobs", "parsed_row_count": 0},
        ),
    )

    row = {"run_key": "run-1", "branch_query": _branch().query, **calls[0]}
    insert_provider_calls(db_path=str(tmp_path / "analytics.duckdb"), **row)
    connection = duckdb.connect(str(tmp_path / "analytics.duckdb"), read_only=True)
    try:
        result = connection.execute(
            "SELECT branch_query, request_query, http_status, result_class, response_meta_json "
            "FROM provider_calls"
        ).fetchone()
    finally:
        connection.close()

    assert result == (
        "repo:acme/demo needle",
        "needle",
        200,
        "empty",
        '{"scope": "blobs", "parsed_row_count": 0}',
    )
