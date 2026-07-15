from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from live_web_search_quality_support import _find_key, _json_object


def analytics_integrity(
    calls: list[dict[str, Any]], exported: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    runs = exported.get("search_runs", [])
    call_queries = {record["query"]: record for record in calls}
    runs_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        runs_by_query[str(row.get("query"))].append(row)
    missing_queries = sorted(set(call_queries) - set(runs_by_query))
    unexpected_queries = sorted(set(runs_by_query) - set(call_queries))
    duplicate_queries = sorted(query for query, rows in runs_by_query.items() if len(rows) != 1)
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for table in (
        "search_branches",
        "provider_calls",
        "search_candidates",
        "final_results",
        "rerank_stages",
        "search_quality_scores",
    ):
        for row in exported.get(table, []):
            grouped[str(row.get("run_key"))][table] += 1
    row_mismatches: list[dict[str, Any]] = []
    for run in runs:
        run_key = str(run["run_key"])
        counts = grouped[run_key]
        expected = {
            "search_branches": run.get("branch_count"),
            "search_candidates": run.get("candidate_count"),
            "final_results": run.get("final_result_count"),
        }
        for table, expected_count in expected.items():
            if expected_count is not None and counts[table] != expected_count:
                row_mismatches.append(
                    {
                        "run_key": run_key,
                        "query": run.get("query"),
                        "table": table,
                        "expected": expected_count,
                        "actual": counts[table],
                    }
                )
        if counts["provider_calls"] < 1 or counts["search_quality_scores"] != 1:
            row_mismatches.append(
                {
                    "run_key": run_key,
                    "query": run.get("query"),
                    "table": "provider_calls/search_quality_scores",
                    "expected": ">=1/1",
                    "actual": f"{counts['provider_calls']}/{counts['search_quality_scores']}",
                }
            )
    successful_missing = [
        record["id"]
        for record in calls
        if record["status"] == "success" and record["query"] in missing_queries
    ]
    late_completions = [
        record["id"]
        for record in calls
        if record["status"] != "success"
        and any(row.get("status") == "success" for row in runs_by_query.get(record["query"], []))
    ]
    diagnostic_embedding_runs = [
        row["run_key"]
        for row in runs
        if _find_key(_json_object(row.get("payload_json")), "query_embedding_dim") == 1024
    ]
    embedding_rows = exported.get("embedding_coverage", [])
    query_embedding_rows = [
        row for row in embedding_rows if row.get("embedding_table") == "query_embeddings"
    ]
    return {
        "table_counts": {table: len(rows) for table, rows in exported.items()},
        "missing_search_run_count": len(missing_queries),
        "missing_search_run_ids": [call_queries[query]["id"] for query in missing_queries],
        "successful_calls_missing_search_run": successful_missing,
        "unexpected_search_run_queries": unexpected_queries,
        "duplicate_search_run_queries": duplicate_queries,
        "late_server_completions_after_client_failure": late_completions,
        "row_mismatches": row_mismatches,
        "rerank_rows_with_null_status": sum(
            row.get("status") is None for row in exported.get("rerank_stages", [])
        ),
        "diagnostic_1024d_embedding_runs": len(diagnostic_embedding_runs),
        "query_embedding_rows": len(query_embedding_rows),
        "query_embedding_dimensions": dict(
            Counter(str(row.get("vector_dimension")) for row in query_embedding_rows)
        ),
    }
