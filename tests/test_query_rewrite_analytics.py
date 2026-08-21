"""Persistence coverage for planner variants and provider query shaping."""

from __future__ import annotations

from pathlib import Path

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import (
    ensure_store_schema,
    insert_funnel_uplift_batches,
    insert_search_branches,
)
from kindly_web_search_mcp_server.analytics.views import ensure_views


class TestQueryRewriteAnalytics:
    def test_variants_and_transforms_preserve_branch_lineage(self, tmp_path: Path) -> None:
        db_path = tmp_path / "analytics.duckdb"
        ensure_store_schema(db_path=str(db_path))

        run_key = "run-1"
        branches = [
            (0, "original_free", "original query"),
            (1, "paid_brave", "expanded query"),
        ]
        for branch_index, branch_role, branch_query in branches:
            insert_search_branches(
                db_path=str(db_path),
                run_key=run_key,
                branch_index=branch_index,
                branch_id=f"branch-{branch_index}",
                branch_role=branch_role,
                branch_query=branch_query,
                branch_why="test branch",
                support_terms=[],
                max_results=5,
                assigned_providers=["provider-a"],
                attempted_providers=["provider-a"],
                skipped_providers=[],
                results_count=1,
                latency_ms=1.0,
                payload_json={},
            )

        insert_funnel_uplift_batches(
            db_path=str(db_path),
            query_variants=[
                {
                    "variant_id": "variant-0",
                    "run_key": run_key,
                    "variant_order": 0,
                    "variant_role": "original_free",
                    "query_text": "original query",
                    "branch_id": "branch-0",
                    "selected": True,
                    "executed": True,
                    "skip_reason": None,
                },
                {
                    "variant_id": "variant-1",
                    "run_key": run_key,
                    "variant_order": 1,
                    "variant_role": "paid_brave",
                    "query_text": "expanded query",
                    "branch_id": "branch-1",
                    "selected": True,
                    "executed": True,
                    "skip_reason": None,
                },
            ],
            provider_results=[
                {
                    "provider_result_id": "provider-result-0",
                    "provider_call_id": "call-0",
                    "run_key": run_key,
                    "branch_id": "branch-0",
                    "provider": "provider-a",
                    "provider_rank": 1,
                    "canonical_result_id": "result-0",
                    "raw_url": "https://example.com/original",
                    "title": "Original",
                    "snippet": "Original candidate",
                    "raw_score": None,
                    "is_eligible": True,
                    "rejection_reason": None,
                    "payload_json": {},
                },
                {
                    "provider_result_id": "provider-result-1",
                    "provider_call_id": "call-1",
                    "run_key": run_key,
                    "branch_id": "branch-1",
                    "provider": "provider-a",
                    "provider_rank": 1,
                    "canonical_result_id": "result-1",
                    "raw_url": "https://example.com/rewrite",
                    "title": "Rewrite",
                    "snippet": "Rewrite candidate",
                    "raw_score": None,
                    "is_eligible": True,
                    "rejection_reason": None,
                    "payload_json": {},
                },
            ],
            query_transforms=[
                {
                    "transform_id": "transform-1",
                    "run_key": run_key,
                    "branch_id": "branch-1",
                    "branch_index": 1,
                    "branch_role": "paid_brave",
                    "provider": "provider-a",
                    "provider_call_id": "call-1",
                    "original_query": "expanded query",
                    "shaped_query": "expanded query site:example.com",
                    "changed": True,
                    "rules_applied": ["provider.site_scope"],
                    "metadata_json": {"scope": "example.com"},
                }
            ],
        )

        ensure_views(db_path=str(db_path))
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            assert con.execute(
                "SELECT variant_role, branch_id, executed, skip_reason "
                "FROM query_variants ORDER BY variant_order"
            ).fetchall() == [
                ("original_free", "branch-0", True, None),
                ("paid_brave", "branch-1", True, None),
            ]
            transform = con.execute(
                "SELECT original_query, shaped_query, changed, rules_applied, metadata_json "
                "FROM query_transforms"
            ).fetchone()
            assert transform[:4] == (
                "expanded query",
                "expanded query site:example.com",
                True,
                ["provider.site_scope"],
            )
            assert transform[4] == '{"scope": "example.com"}'
            assert con.execute(
                "SELECT variant_role, branches, discovered_unique "
                "FROM vw_rewrite_value ORDER BY variant_role"
            ).fetchall() == [
                ("original_free", 1, 1),
                ("paid_brave", 1, 1),
            ]
        finally:
            con.close()
