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
            (0, "original", "original query"),
            (1, "free", "expanded query"),
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
                    "variant_role": "original",
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
                    "variant_role": "free",
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
                    "branch_role": "free",
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
                ("original", "branch-0", True, None),
                ("free", "branch-1", True, None),
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
                ("free", 1, 1),
                ("original", 1, 1),
            ]
        finally:
            con.close()

    def test_schema_bootstrap_migrates_legacy_branch_role_values(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-roles.duckdb"
        ensure_store_schema(db_path=str(db_path))
        con = duckdb.connect(str(db_path))
        try:
            con.execute(
                """
                INSERT INTO search_branches (run_key, branch_index, branch_role, branch_query)
                VALUES ('legacy-run', 0, 'original_free', 'query')
                """
            )
            con.execute(
                """
                INSERT INTO provider_calls (run_key, branch_role, provider, status)
                VALUES ('legacy-run', 'paid_brave', 'brave', 'success')
                """
            )
            con.execute(
                """
                INSERT INTO query_variants
                    (variant_id, run_key, variant_order, variant_role, query_text)
                VALUES ('legacy-variant', 'legacy-run', 0, 'neural', 'query')
                """
            )
            con.execute(
                """
                INSERT INTO query_transforms
                    (transform_id, run_key, branch_id, branch_index, branch_role,
                     provider, provider_call_id, original_query, shaped_query, changed)
                VALUES ('legacy-transform', 'legacy-run', 'legacy-branch', 0,
                        'specialized', 'exa', 'legacy-call', 'query', 'query', false)
                """
            )
        finally:
            con.close()

        ensure_store_schema(db_path=str(db_path))
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            assert con.execute(
                "SELECT branch_role FROM search_branches WHERE run_key = 'legacy-run'"
            ).fetchone() == ("original",)
            assert con.execute(
                "SELECT branch_role FROM provider_calls WHERE run_key = 'legacy-run'"
            ).fetchone() == ("free",)
            assert con.execute(
                "SELECT variant_role FROM query_variants WHERE run_key = 'legacy-run'"
            ).fetchone() == ("semantic_tavily",)
            assert con.execute(
                "SELECT branch_role FROM query_transforms WHERE run_key = 'legacy-run'"
            ).fetchone() == ("semantic_exa",)
        finally:
            con.close()
