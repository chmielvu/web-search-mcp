"""Unit and integration tests for planner graph expansion consumer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import duckdb

from kindly_web_search_mcp_server.analytics.duckdb_store import ensure_store_schema
from kindly_web_search_mcp_server.analytics.graph_feedback import (
    GraphBuildConfig,
    build_graph_snapshot,
    publish_graph_snapshot,
)
from kindly_web_search_mcp_server.analytics.writers.core import insert_result_labels
from kindly_web_search_mcp_server.search.contracts import (
    BranchRole,
    SearchRun,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.graph_expansion import (
    GraphExpansionDecision,
    expand_seed_queries,
)
from kindly_web_search_mcp_server.prompts.query_rewrite import RewrittenQueries
from kindly_web_search_mcp_server.search.planning import plan_search
from kindly_web_search_mcp_server.settings import settings


class TestGraphExpansionUnit:
    """Pure unit tests for expand_seed_queries behavior."""

    def test_no_capacity_when_four_seeds_provided(self) -> None:
        base_seeds = ("seed 1", "seed 2", "seed 3", "seed 4")
        decision = expand_seed_queries(
            normalized_query="seed 1",
            base_seed_queries=base_seeds,
            enabled=True,
            max_related_queries=2,
            max_age_seconds=86400,
        )
        assert decision.status == "no_capacity"
        assert decision.effective_seed_queries == base_seeds
        assert decision.related_queries == ()
        assert decision.generation_id is None

    def test_disabled_returns_base_seeds(self) -> None:
        base_seeds = ("base query",)
        decision = expand_seed_queries(
            normalized_query="base query",
            base_seed_queries=base_seeds,
            enabled=False,
            max_related_queries=2,
            max_age_seconds=86400,
        )
        assert decision.status == "disabled"
        assert decision.effective_seed_queries == base_seeds
        assert decision.related_queries == ()

    def test_missing_or_stale_index_returns_unavailable(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_missing.duckdb"
        decision = expand_seed_queries(
            normalized_query="unknown query",
            base_seed_queries=("unknown query",),
            enabled=True,
            max_related_queries=2,
            max_age_seconds=86400,
            db_path=str(db_file),
        )
        assert decision.status == "unavailable"
        assert decision.effective_seed_queries == ("unknown query",)

    def test_applied_with_indexed_neighbor(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test_expansion.duckdb")
        ensure_store_schema(db_path=db_path)
        now = datetime.now(timezone.utc)

        # Seed 2 queries sharing 2 documents
        con = duckdb.connect(db_path, read_only=False)
        con.execute(
            """
            INSERT INTO search_runs (run_key, query, normalized_query, recorded_at) VALUES
            ('rk1', 'quantum computing basics', 'quantum computing basics', ?),
            ('rk2', 'introduction to qubits', 'introduction to qubits', ?)
            """,
            [now, now],
        )
        con.close()

        labels = [
            {
                "run_key": "rk1",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "doc_q1",
                "raw_url": "https://example.com/q1",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            {
                "run_key": "rk1",
                "position": 1,
                "label": 1.0,
                "canonical_result_id": "doc_q2",
                "raw_url": "https://example.com/q2",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            {
                "run_key": "rk2",
                "position": 0,
                "label": 1.0,
                "canonical_result_id": "doc_q1",
                "raw_url": "https://example.com/q1",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
            {
                "run_key": "rk2",
                "position": 1,
                "label": 1.0,
                "canonical_result_id": "doc_q2",
                "raw_url": "https://example.com/q2",
                "source": "llm_judge",
                "rubric_version": "v1",
                "recorded_at": now,
            },
        ]
        insert_result_labels(labels, db_path=db_path, sync=True)

        config = GraphBuildConfig(source_cutoff=now, min_shared_documents=2, max_related_queries=2)
        snapshot = build_graph_snapshot(db_path=db_path, config=config)
        publish_graph_snapshot(snapshot, db_path=db_path)

        # Clear memory cache to ensure read from DB
        from kindly_web_search_mcp_server.analytics import graph_feedback

        with graph_feedback._CACHE_LOCK:
            graph_feedback._CACHED_INDEX = None
            graph_feedback._CACHED_AT = 0.0

        decision = expand_seed_queries(
            normalized_query="quantum computing basics",
            base_seed_queries=("quantum computing basics",),
            enabled=True,
            max_related_queries=2,
            max_age_seconds=86400,
            db_path=db_path,
        )

        assert decision.status == "applied"
        assert decision.generation_id == snapshot.generation_id
        assert decision.related_queries == ("introduction to qubits",)
        assert decision.effective_seed_queries == (
            "quantum computing basics",
            "introduction to qubits",
        )

    def test_loader_error_returns_error_status(self) -> None:
        with patch(
            "kindly_web_search_mcp_server.search.graph_expansion.load_latest_graph_index",
            side_effect=RuntimeError("Database disk failure"),
        ):
            decision = expand_seed_queries(
                normalized_query="test query",
                base_seed_queries=("test query",),
                enabled=True,
                max_related_queries=2,
                max_age_seconds=86400,
            )
            assert decision.status == "error"
            assert decision.error_type == "RuntimeError"
            assert decision.effective_seed_queries == ("test query",)


@pytest.mark.asyncio
class TestPlanSearchGraphExpansionIntegration:
    """Integration tests verifying plan_search wiring with graph expansion."""

    async def test_plan_search_passes_effective_seeds_when_expansion_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "graph_expansion_enabled", True)
        monkeypatch.setattr(settings, "graph_expansion_max_related_queries", 2)
        monkeypatch.setattr(settings, "graph_expansion_max_age_seconds", 86400.0)

        mock_decision = GraphExpansionDecision(
            status="applied",
            generation_id="gen_test_123",
            base_seed_queries=("python async",),
            effective_seed_queries=("python async", "asyncio event loop"),
            related_queries=("asyncio event loop",),
        )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.search.planning.expand_seed_queries",
            lambda **_kwargs: mock_decision,
        )

        captured_rewrite_kwargs: dict[str, object] = {}

        async def mock_rewrite(**kwargs):
            captured_rewrite_kwargs.update(kwargs)
            return (
                RewrittenQueries(
                    free="q free",
                    serp1="q serp1",
                    serp2="q serp2",
                    semantic_tavily="q tavily",
                    semantic_exa="q exa",
                ),
                {"model": "test-rewriter", "latency_ms": 50.0},
            )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.search.planning._rewrite_queries",
            mock_rewrite,
        )

        req = WebSearchRequest(
            query="python async",
            research_goal="understand coroutines",
            rewrite=True,
        )
        run = SearchRun(
            run_key="rk_plan_test_001",
            request=req,
            session_id="s1",
            http_client=MagicMock(),
        )

        plan = await plan_search(run)

        # 1. Assert _rewrite_queries received effective seed queries
        assert captured_rewrite_kwargs["seed_queries"] == (
            "python async",
            "asyncio event loop",
        )

        # 2. Assert SearchPlan.seed_queries has effective seeds
        assert plan.seed_queries == ("python async", "asyncio event loop")

        # 3. Assert provider_arguments["gemma"]["queries"] has effective seeds
        gemma_args = plan.provider_arguments.get("gemma", {})
        assert gemma_args.get("queries") == ["python async", "asyncio event loop"]

        # 4. Assert 6 branches and 6 query_variant_rows
        assert len(plan.branches) == 6
        assert len(run.diagnostics.query_variant_rows) == 6

        # 5. Assert diagnostics rewrite_metadata has bounded graph_expansion
        assert run.diagnostics.rewrite_metadata is not None
        gx_meta = run.diagnostics.rewrite_metadata.get("graph_expansion")
        assert gx_meta is not None
        assert gx_meta["status"] == "applied"
        assert gx_meta["generation_id"] == "gen_test_123"
        assert gx_meta["related_queries"] == ["asyncio event loop"]

    async def test_plan_search_disabled_flag_does_not_expand(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "graph_expansion_enabled", False)

        async def mock_rewrite(**_kwargs):
            return (
                RewrittenQueries(
                    free="q free",
                    serp1="q serp1",
                    serp2="q serp2",
                    semantic_tavily="q tavily",
                    semantic_exa="q exa",
                ),
                {"model": "test-rewriter"},
            )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.search.planning._rewrite_queries",
            mock_rewrite,
        )

        req = WebSearchRequest(
            query="rust ownership",
            research_goal="learn borrow checker",
            rewrite=True,
        )
        run = SearchRun(
            run_key="rk_plan_test_002",
            request=req,
            session_id="s2",
            http_client=MagicMock(),
        )

        plan = await plan_search(run)

        assert plan.seed_queries == ("rust ownership",)
        assert run.diagnostics.rewrite_metadata is not None
        gx_meta = run.diagnostics.rewrite_metadata.get("graph_expansion")
        assert gx_meta is not None
        assert gx_meta["status"] == "disabled"

    async def test_plan_search_rewrite_exception_retains_fallback_and_why(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "graph_expansion_enabled", True)

        mock_decision = GraphExpansionDecision(
            status="applied",
            generation_id="gen_test_456",
            base_seed_queries=("golang channels",),
            effective_seed_queries=("golang channels", "goroutine synchronization"),
            related_queries=("goroutine synchronization",),
        )

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.search.planning.expand_seed_queries",
            lambda **_kwargs: mock_decision,
        )

        async def mock_failing_rewrite(**_kwargs):
            raise TimeoutError("LLM rewrite timed out")

        monkeypatch.setattr(
            "kindly_web_search_mcp_server.search.planning._rewrite_queries",
            mock_failing_rewrite,
        )

        req = WebSearchRequest(
            query="golang channels",
            research_goal="concurrency patterns",
            rewrite=True,
        )
        run = SearchRun(
            run_key="rk_plan_test_003",
            request=req,
            session_id="s3",
            http_client=MagicMock(),
        )

        plan = await plan_search(run)

        # 6 branches still produced using fallback
        assert len(plan.branches) == 6
        assert len(run.diagnostics.query_variant_rows) == 6

        # rewrite_metadata has error and graph_expansion
        assert run.diagnostics.rewrite_metadata is not None
        assert run.diagnostics.rewrite_metadata.get("error") == "TimeoutError"
        gx = run.diagnostics.rewrite_metadata.get("graph_expansion")
        assert isinstance(gx, dict)
        assert gx.get("status") == "applied"

        # Deterministic why strings for paid/neural/specialized branches
        for b in plan.branches:
            if b.role != BranchRole.ORIGINAL:
                assert b.why.startswith("deterministic")
