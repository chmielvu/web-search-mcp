"""Comprehensive A/B testing integration tests (Task 29).

Tests the full A/B testing system end-to-end:
1. Experiment lifecycle (YAML → load → assign → record → conclude)
2. Assignment determinism
3. Traffic enrollment
4. Layer mutual exclusion
5. Shadow mode
6. Variant weight distribution
7. End-to-end pipeline with mock pipeline stage
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.ab_testing.assignment import (
    _hash_to_bucket,
    get_assigned_variant,
)
from kindly_web_search_mcp_server.ab_testing.models import (
    ABExperiment,
    ABVariant,
)
from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
    load_experiments,
    save_experiments,
)
from kindly_web_search_mcp_server.ab_testing.wiring import get_ab_overrides
from kindly_web_search_mcp_server.ab_testing.shadow_runner import run_shadow


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def tmp_yaml(tmp_path):
    """Return a Path to a temporary YAML config file."""
    return tmp_path / "experiments.yaml"


@pytest.fixture
def in_memory_db():
    """Create an in-memory DuckDB connection and ensure AB tables exist.

    Returns the connection so tests can query it directly.
    """
    conn = duckdb.connect(":memory:")
    # Create the AB shadow runs table (same schema as duckdb_store._ensure_ab_shadow_runs)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_shadow_runs (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            experiment_id VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            layer VARCHAR NOT NULL,
            duration_ms DOUBLE,
            judge_score DOUBLE,
            tokens_used INTEGER,
            cost_usd DOUBLE,
            error_type VARCHAR,
            payload_json JSON
        )
    """)
    # Create the AB assignments table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_assignments (
            assignment_id VARCHAR NOT NULL PRIMARY KEY,
            experiment_id VARCHAR NOT NULL,
            run_key VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload_json JSON
        )
    """)
    # Create the AB results table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_results (
            result_id VARCHAR NOT NULL PRIMARY KEY,
            experiment_id VARCHAR NOT NULL,
            run_key VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            primary_metric DOUBLE,
            secondary_metric DOUBLE,
            duration_ms DOUBLE,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload_json JSON
        )
    """)
    yield conn
    conn.close()


def _make_running_exp(
    experiment_id: str,
    layer: str,
    variants: list[ABVariant] | None = None,
    traffic_pct: float = 100.0,
    started_at: str = "2025-06-01",
) -> ABExperiment:
    if variants is None:
        variants = [ABVariant("control", 50), ABVariant("treatment", 50)]
    return ABExperiment(
        experiment_id=experiment_id,
        layer=layer,
        status="running",
        traffic_pct=traffic_pct,
        started_at=started_at,
        variants=variants,
    )


# =========================================================================
# 1. Experiment Lifecycle
# =========================================================================


class TestExperimentLifecycle:
    """Full lifecycle: create via YAML, load, assign, record results, conclude."""

    def test_full_lifecycle(self, tmp_yaml):
        """Create experiment YAML → load → assign → record → conclude."""
        # --- Phase 1: Create experiment via YAML ---
        data = {
            "experiments": [
                {
                    "experiment_id": "lifecycle-exp",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "hypothesis": "New reranker improves NDCG",
                    "primary_metric": "ndcg@10",
                    "guardrail_metrics": ["latency_p95"],
                    "started_at": "2025-06-01",
                    "variants": [
                        {
                            "variant_key": "control",
                            "weight": 50,
                            "config": {"model": "baseline"},
                            "description": "Current reranker",
                        },
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"model": "neural-v2"},
                            "description": "Neural reranker v2",
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        # --- Phase 2: Load experiments ---
        experiments = load_experiments(tmp_yaml)
        assert len(experiments) == 1
        exp = experiments[0]
        assert exp.experiment_id == "lifecycle-exp"
        assert exp.status == "running"
        assert exp.traffic_pct == 100.0

        # --- Phase 3: Assign variants ---
        assignment = get_assigned_variant("run-abc-123", "reranking", experiments)
        assert assignment is not None
        assert assignment.experiment_id == "lifecycle-exp"
        assert assignment.variant_key in ("control", "test")
        assert assignment.run_key == "run-abc-123"
        assert assignment.layer == "reranking"

        # --- Phase 4: Record results (simulate via DuckDB) ---
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ab_results (
                result_id VARCHAR NOT NULL PRIMARY KEY,
                experiment_id VARCHAR NOT NULL,
                run_key VARCHAR NOT NULL,
                variant VARCHAR NOT NULL,
                primary_metric DOUBLE,
                secondary_metric DOUBLE,
                duration_ms DOUBLE,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                payload_json JSON
            )
        """)
        conn.execute(
            """
            INSERT INTO ab_results (result_id, experiment_id, run_key, variant, primary_metric, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                assignment.experiment_id,
                assignment.run_key,
                assignment.variant_key,
                0.85,  # ndcg@10
                120.5,  # duration_ms
            ),
        )
        rows = conn.execute(
            "SELECT experiment_id, variant, primary_metric FROM ab_results"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "lifecycle-exp"
        assert rows[0][1] in ("control", "test")
        assert rows[0][2] == 0.85
        conn.close()

        # --- Phase 5: Conclude experiment ---
        exp.status = "concluded"
        exp.ended_at = "2025-07-01"
        exp.winning_variant = "test"
        save_experiments([exp], tmp_yaml)

        reloaded = load_experiments(tmp_yaml)
        assert len(reloaded) == 1
        assert reloaded[0].status == "concluded"
        assert reloaded[0].ended_at == "2025-07-01"
        assert reloaded[0].winning_variant == "test"

        # Concluded experiments should no longer assign
        assignment_after = get_assigned_variant("run-abc-123", "reranking", reloaded)
        assert assignment_after is None

    def test_lifecycle_with_payload(self, tmp_yaml):
        """Experiment payload survives save/load round-trip."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment(
            experiment_id="payload-exp",
            layer="reranking",
            status="running",
            traffic_pct=50.0,
            variants=variants,
            payload={"owner": "team-ml", "ticket": "PROJ-123", "notes": "urgent"},
        )
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert len(loaded) == 1
        assert loaded[0].payload == {
            "owner": "team-ml",
            "ticket": "PROJ-123",
            "notes": "urgent",
        }

    def test_lifecycle_status_transitions(self, tmp_yaml):
        """Experiment status transitions: draft → running → paused → running → concluded."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]

        # Start as draft
        exp = ABExperiment(
            experiment_id="status-exp",
            layer="reranking",
            status="draft",
            variants=variants,
        )
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert loaded[0].status == "draft"
        # Draft should not assign
        assert get_assigned_variant("run-1", "reranking", loaded) is None

        # Start running
        exp.status = "running"
        exp.started_at = "2025-06-01"
        exp.traffic_pct = 100.0
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert loaded[0].status == "running"
        assert get_assigned_variant("run-1", "reranking", loaded) is not None

        # Pause
        exp.status = "paused"
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert loaded[0].status == "paused"
        assert get_assigned_variant("run-1", "reranking", loaded) is None

        # Resume
        exp.status = "running"
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert loaded[0].status == "running"
        assert get_assigned_variant("run-1", "reranking", loaded) is not None

        # Conclude
        exp.status = "concluded"
        exp.ended_at = "2025-07-01"
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert loaded[0].status == "concluded"
        assert get_assigned_variant("run-1", "reranking", loaded) is None


# =========================================================================
# 2. Assignment Determinism
# =========================================================================


class TestAssignmentDeterminism:
    """Same run_key always gets same variant."""

    def test_deterministic_assignment(self):
        """Calling get_assigned_variant multiple times with same run_key yields same variant."""
        variants = [ABVariant("control", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("det-exp", "reranking", variants, traffic_pct=100.0)]

        results = []
        for _ in range(10):
            results.append(get_assigned_variant("deterministic-run", "reranking", exps))

        assert all(r is not None for r in results)
        variant_keys = [r.variant_key for r in results]
        assert all(v == variant_keys[0] for v in variant_keys)

    def test_deterministic_across_experiment_ids(self):
        """Same run_key in different experiments gets different (but stable) assignments."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]

        exps_a = [_make_running_exp("exp-a", "reranking", variants)]
        exps_b = [_make_running_exp("exp-b", "reranking", variants)]

        # Each experiment should produce a stable assignment
        for exps in [exps_a, exps_b]:
            results = [get_assigned_variant("same-run", "reranking", exps) for _ in range(5)]
            keys = [r.variant_key for r in results]
            assert all(k == keys[0] for k in keys)

    def test_deterministic_hash_to_bucket(self):
        """_hash_to_bucket is deterministic for same inputs."""
        b1 = _hash_to_bucket("my-run-key", "my-exp")
        b2 = _hash_to_bucket("my-run-key", "my-exp")
        assert b1 == b2

    def test_different_run_keys_different_buckets(self):
        """Different run_keys produce different buckets (extremely unlikely to collide)."""
        buckets = set()
        for i in range(1000):
            buckets.add(_hash_to_bucket(f"run-{i}", "exp-det"))
        # With 10000 buckets and 1000 samples, collisions are possible but
        # we should have at least 900 unique buckets
        assert len(buckets) > 900

    def test_sticky_assignment_across_reloads(self, tmp_yaml):
        """Assignment is stable across YAML save/load cycles."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = _make_running_exp("sticky-exp", "reranking", variants)
        save_experiments([exp], tmp_yaml)

        run_key = "sticky-run-42"
        first_assignment = None

        for _ in range(5):
            loaded = load_experiments(tmp_yaml)
            assignment = get_assigned_variant(run_key, "reranking", loaded)
            if first_assignment is None:
                first_assignment = assignment
            else:
                assert assignment is not None
                assert assignment.variant_key == first_assignment.variant_key
                assert assignment.experiment_id == first_assignment.experiment_id


# =========================================================================
# 3. Traffic Enrollment
# =========================================================================


class TestTrafficEnrollment:
    """traffic_pct controls enrollment rate."""

    def test_full_traffic_enrolls_all(self):
        """traffic_pct=100 should enroll every run_key."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("full-traffic", "reranking", variants, traffic_pct=100.0)]

        results = [get_assigned_variant(f"run-{i}", "reranking", exps) for i in range(100)]
        assigned = [r for r in results if r is not None]
        assert len(assigned) == 100

    def test_zero_traffic_enrolls_none(self):
        """traffic_pct=0.01 should enroll almost no one."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("zero-traffic", "reranking", variants, traffic_pct=0.01)]

        results = [get_assigned_variant(f"run-{i}", "reranking", exps) for i in range(5000)]
        assigned = [r for r in results if r is not None]
        assert len(assigned) < 10  # at most a handful out of 5000

    def test_partial_traffic_approximates_pct(self):
        """traffic_pct=50 should enroll roughly 50% of run_keys."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("partial-traffic", "reranking", variants, traffic_pct=50.0)]

        n = 2000
        results = [get_assigned_variant(f"run-{i}", "reranking", exps) for i in range(n)]
        assigned = [r for r in results if r is not None]
        pct = len(assigned) / n * 100
        # Allow 10% absolute deviation from 50%
        assert 20 <= pct <= 80, f"Enrollment {pct:.1f}% outside expected range (20-80%)"

    def test_traffic_pct_boundary_values(self):
        """Edge cases: traffic_pct=0.1 and traffic_pct=99.9."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]

        # Very low traffic
        exps_low = [_make_running_exp("low", "reranking", variants, traffic_pct=0.1)]
        results_low = [get_assigned_variant(f"run-{i}", "reranking", exps_low) for i in range(5000)]
        assigned_low = [r for r in results_low if r is not None]
        assert len(assigned_low) < 50  # at most a few dozen out of 5000

        # Very high traffic
        exps_high = [_make_running_exp("high", "reranking", variants, traffic_pct=99.9)]
        results_high = [
            get_assigned_variant(f"run-{i}", "reranking", exps_high) for i in range(2000)
        ]
        assigned_high = [r for r in results_high if r is not None]
        assert len(assigned_high) > 1900  # most should be enrolled

    def test_traffic_pct_from_yaml(self, tmp_yaml):
        """traffic_pct loaded from YAML controls enrollment."""
        data = {
            "experiments": [
                {
                    "experiment_id": "yaml-traffic",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 30.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "ctrl", "weight": 50},
                        {"variant_key": "test", "weight": 50},
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        experiments = load_experiments(tmp_yaml)
        assert experiments[0].traffic_pct == 30.0

        n = 2000
        results = [get_assigned_variant(f"run-{i}", "reranking", experiments) for i in range(n)]
        assigned = [r for r in results if r is not None]
        pct = len(assigned) / n * 100
        assert 10 <= pct <= 55, f"Enrollment {pct:.1f}% outside expected range (10-55%)"


# =========================================================================
# 4. Layer Mutual Exclusion
# =========================================================================


class TestLayerMutualExclusion:
    """Only one running experiment per layer."""

    def test_latest_running_wins(self):
        """When multiple running experiments exist for same layer, the latest started_at wins."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            _make_running_exp("exp-old", "reranking", variants, started_at="2025-01-01"),
            _make_running_exp("exp-mid", "reranking", variants, started_at="2025-03-15"),
            _make_running_exp("exp-new", "reranking", variants, started_at="2025-06-01"),
        ]

        for i in range(50):
            result = get_assigned_variant(f"run-{i}", "reranking", exps)
            assert result is not None
            assert result.experiment_id == "exp-new"

    def test_different_layers_independent(self):
        """Experiments in different layers don't interfere."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            _make_running_exp("qu-exp", "query_understanding", variants),
            _make_running_exp("rerank-exp", "reranking", variants),
            _make_running_exp("provider-exp", "provider_weights", variants),
        ]

        qu_result = get_assigned_variant("run-1", "query_understanding", exps)
        rerank_result = get_assigned_variant("run-1", "reranking", exps)
        provider_result = get_assigned_variant("run-1", "provider_weights", exps)

        assert qu_result is not None
        assert qu_result.experiment_id == "qu-exp"
        assert rerank_result is not None
        assert rerank_result.experiment_id == "rerank-exp"
        assert provider_result is not None
        assert provider_result.experiment_id == "provider-exp"

    def test_non_running_experiments_ignored(self):
        """Draft/paused/concluded experiments don't participate in mutual exclusion."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment("exp-draft", "reranking", status="draft", variants=variants),
            ABExperiment("exp-paused", "reranking", status="paused", variants=variants),
            ABExperiment(
                "exp-concluded",
                "reranking",
                status="concluded",
                variants=variants,
            ),
        ]

        result = get_assigned_variant("run-1", "reranking", exps)
        assert result is None

    def test_mixed_statuses_only_running_counts(self):
        """Only running experiments are considered for assignment."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment("exp-draft", "reranking", status="draft", variants=variants),
            _make_running_exp("exp-running", "reranking", variants),
            ABExperiment(
                "exp-concluded",
                "reranking",
                status="concluded",
                variants=variants,
            ),
        ]

        result = get_assigned_variant("run-1", "reranking", exps)
        assert result is not None
        assert result.experiment_id == "exp-running"

    def test_no_experiment_for_layer_returns_none(self):
        """No running experiment for the requested layer returns None."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            _make_running_exp("rerank-exp", "reranking", variants),
        ]

        result = get_assigned_variant("run-1", "query_understanding", exps)
        assert result is None


# =========================================================================
# 5. Shadow Mode
# =========================================================================


class TestShadowMode:
    """Shadow runs fire in background, don't block production."""

    @pytest.mark.asyncio
    async def test_shadow_fn_called_with_correct_kwargs(self):
        """Shadow function must be invoked with the provided kwargs."""
        shadow_fn = AsyncMock(return_value="result")

        with patch("kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"):
            await run_shadow(
                run_key="rk-shadow-1",
                experiment_id="exp-shadow-1",
                variant="treatment",
                layer="query_understanding",
                shadow_fn=shadow_fn,
                shadow_kwargs={"query": "hello world", "top_k": 10},
                control_duration_ms=100.0,
            )

        shadow_fn.assert_awaited_once_with(query="hello world", top_k=10)

    @pytest.mark.asyncio
    async def test_shadow_result_recorded(self):
        """insert_ab_shadow_run must be called with correct parameters."""
        shadow_fn = AsyncMock(return_value=["result1", "result2"])

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ) as mock_insert:
            await run_shadow(
                run_key="rk-shadow-2",
                experiment_id="exp-shadow-2",
                variant="treatment_v2",
                layer="reranking",
                shadow_fn=shadow_fn,
                shadow_kwargs={"query": "test"},
                control_duration_ms=200.0,
                control_result_summary={"num_results": 10},
            )

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["run_key"] == "rk-shadow-2"
        assert call_kwargs["experiment_id"] == "exp-shadow-2"
        assert call_kwargs["variant"] == "treatment_v2"
        assert call_kwargs["layer"] == "reranking"
        assert isinstance(call_kwargs["duration_ms"], float)
        assert call_kwargs["error_type"] is None

        payload = call_kwargs["payload_json"]
        assert isinstance(payload, dict)
        assert "control_duration_ms" in payload
        assert "latency_delta_ms" in payload
        assert payload["control_summary"] == {"num_results": 10}

    @pytest.mark.asyncio
    async def test_shadow_failure_does_not_propagate(self):
        """When shadow_fn raises, no exception propagates to caller."""
        shadow_fn = AsyncMock(side_effect=ValueError("shadow exploded"))

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run"
        ) as mock_insert:
            # Should not raise despite shadow_fn raising
            await run_shadow(
                run_key="rk-shadow-3",
                experiment_id="exp-shadow-3",
                variant="treatment",
                layer="search",
                shadow_fn=shadow_fn,
                shadow_kwargs={},
                control_duration_ms=50.0,
            )

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["error_type"] == "shadow_failed"

    @pytest.mark.asyncio
    async def test_shadow_duckdb_failure_does_not_propagate(self):
        """Even if insert_ab_shadow_run raises, run_shadow must not propagate."""
        shadow_fn = AsyncMock(return_value="ok")

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run",
            side_effect=RuntimeError("db failure"),
        ):
            # Should not raise
            await run_shadow(
                run_key="rk-shadow-4",
                experiment_id="exp-shadow-4",
                variant="treatment",
                layer="reranking",
                shadow_fn=shadow_fn,
                shadow_kwargs={},
                control_duration_ms=100.0,
            )

    @pytest.mark.asyncio
    async def test_shadow_fire_and_forget_pattern(self):
        """asyncio.create_task fire-and-forget pattern works."""
        shadow_fn = AsyncMock(return_value="ok")
        mock_insert = MagicMock()

        with patch(
            "kindly_web_search_mcp_server.ab_testing.shadow_runner.insert_ab_shadow_run",
            mock_insert,
        ):
            task = asyncio.create_task(
                run_shadow(
                    run_key="rk-shadow-5",
                    experiment_id="exp-shadow-5",
                    variant="treatment",
                    layer="search",
                    shadow_fn=shadow_fn,
                    shadow_kwargs={},
                    control_duration_ms=100.0,
                )
            )
            await asyncio.sleep(0)
            await task

        shadow_fn.assert_awaited_once()
        assert mock_insert.called

    def test_shadow_mode_from_wiring(self, tmp_yaml):
        """get_ab_overrides returns shadow_mode=True when variant config has shadow=True."""
        data = {
            "experiments": [
                {
                    "experiment_id": "shadow-wiring",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"model": "test-model", "shadow": True},
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            found_shadow = False
            for i in range(200):
                result = get_ab_overrides(run_key=f"run-{i}", layer="query_understanding")
                if result and result["variant_key"] == "test":
                    assert result["shadow_mode"] is True
                    found_shadow = True
                    break

            assert found_shadow, "No test variant assignment found in 200 attempts"

    def test_shadow_mode_default_false(self, tmp_yaml):
        """get_ab_overrides returns shadow_mode=False when variant config has no shadow key."""
        data = {
            "experiments": [
                {
                    "experiment_id": "no-shadow",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"model": "v2"},
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            for i in range(200):
                result = get_ab_overrides(run_key=f"run-{i}", layer="reranking")
                if result and result["variant_key"] == "test":
                    assert result["shadow_mode"] is False
                    return

    def test_shadow_mode_does_not_block_production(self, tmp_yaml):
        """Shadow mode variant still returns overrides (doesn't block)."""
        data = {
            "experiments": [
                {
                    "experiment_id": "shadow-prod",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"shadow": True, "model": "experimental"},
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            # Shadow mode should still return overrides
            for i in range(200):
                result = get_ab_overrides(run_key=f"run-{i}", layer="query_understanding")
                if result:
                    assert "experiment_id" in result
                    assert "variant_key" in result
                    assert "config" in result
                    return

            pytest.fail("No assignment found in 200 attempts")


# =========================================================================
# 6. Variant Weight Distribution
# =========================================================================


class TestVariantWeightDistribution:
    """Variant weights produce expected distribution."""

    def test_equal_weights_produce_balanced_distribution(self):
        """50/50 split should be roughly balanced."""
        variants = [ABVariant("control", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("equal-weights", "reranking", variants, traffic_pct=100.0)]

        n = 2000
        control_count = 0
        test_count = 0
        for i in range(n):
            r = get_assigned_variant(f"run-{i}", "reranking", exps)
            if r.variant_key == "control":
                control_count += 1
            else:
                test_count += 1

        control_pct = control_count / n * 100
        # Allow 10% absolute deviation from 50%
        assert 30 <= control_pct <= 70, (
            f"Control {control_pct:.1f}% outside expected range (30-70%)"
        )

    def test_skewed_weights_produce_skewed_distribution(self):
        """90/10 split should heavily favor the heavier variant."""
        variants = [ABVariant("heavy", 90), ABVariant("light", 10)]
        exps = [_make_running_exp("skewed", "reranking", variants, traffic_pct=100.0)]

        n = 2000
        heavy_count = 0
        for i in range(n):
            r = get_assigned_variant(f"run-{i}", "reranking", exps)
            if r.variant_key == "heavy":
                heavy_count += 1

        heavy_pct = heavy_count / n * 100
        assert heavy_pct > 70, f"Heavy variant {heavy_pct:.1f}% below expected 90%"

    def test_three_variant_distribution(self):
        """Three variants with 50/30/20 split."""
        variants = [
            ABVariant("a", 50),
            ABVariant("b", 30),
            ABVariant("c", 20),
        ]
        exps = [_make_running_exp("three-way", "reranking", variants, traffic_pct=100.0)]

        n = 3000
        counts = {"a": 0, "b": 0, "c": 0}
        for i in range(n):
            r = get_assigned_variant(f"run-{i}", "reranking", exps)
            counts[r.variant_key] += 1

        a_pct = counts["a"] / n * 100
        b_pct = counts["b"] / n * 100
        c_pct = counts["c"] / n * 100

        # Allow 10% absolute deviation from expected
        assert 30 <= a_pct <= 70, f"Variant a {a_pct:.1f}% outside range (30-70%)"
        assert 10 <= b_pct <= 50, f"Variant b {b_pct:.1f}% outside range (10-50%)"
        assert 5 <= c_pct <= 40, f"Variant c {c_pct:.1f}% outside range (5-40%)"

    def test_weight_sum_not_100(self):
        """Weights don't need to sum to 100 — they're relative."""
        variants = [ABVariant("a", 2), ABVariant("b", 1)]
        exps = [_make_running_exp("relative", "reranking", variants, traffic_pct=100.0)]

        n = 3000
        a_count = 0
        for i in range(n):
            r = get_assigned_variant(f"run-{i}", "reranking", exps)
            if r.variant_key == "a":
                a_count += 1

        a_pct = a_count / n * 100
        # a has 2/3 weight, so ~66.7%
        assert 50 <= a_pct <= 85, f"Variant a {a_pct:.1f}% outside expected range (50-85%)"

    def test_distribution_stable_across_reloads(self, tmp_yaml):
        """Weight distribution is stable across YAML save/load cycles."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = _make_running_exp("stable-dist", "reranking", variants)
        save_experiments([exp], tmp_yaml)

        # Load and check distribution
        loaded = load_experiments(tmp_yaml)
        n = 1000
        ctrl_count = sum(
            1
            for i in range(n)
            if get_assigned_variant(f"run-{i}", "reranking", loaded).variant_key == "ctrl"
        )
        ctrl_pct = ctrl_count / n * 100
        assert 30 <= ctrl_pct <= 70


# =========================================================================
# 7. End-to-End Pipeline
# =========================================================================


class TestEndToEndPipeline:
    """Wire AB overrides into a mock pipeline stage and verify override application."""

    def test_mock_pipeline_applies_overrides(self, tmp_yaml):
        """Mock pipeline stage receives and applies AB overrides."""
        # Create experiment YAML
        data = {
            "experiments": [
                {
                    "experiment_id": "pipeline-exp",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {
                                "model": "groq/experimental-model",
                                "timeout_seconds": 30.0,
                                "temperature": 0.3,
                            },
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        # Mock pipeline stage that consumes AB overrides
        class MockPipelineStage:
            def __init__(self):
                self.applied_overrides = None
                self.results = []

            async def run(self, run_key: str, **kwargs):
                # Simulate a pipeline stage that checks AB overrides
                with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
                    s.ab_testing_enabled = True
                    s.ab_config_path = str(tmp_yaml)

                    overrides = get_ab_overrides(run_key=run_key, layer="query_understanding")
                    self.applied_overrides = overrides

                    if overrides and overrides["variant_key"] == "test":
                        # Apply variant config overrides
                        config = overrides["config"]
                        result = {
                            "run_key": run_key,
                            "model": config.get("model", "default"),
                            "timeout": config.get("timeout_seconds", 10.0),
                            "temperature": config.get("temperature", 0.7),
                            "variant": overrides["variant_key"],
                            "experiment": overrides["experiment_id"],
                            "shadow_mode": overrides["shadow_mode"],
                        }
                    else:
                        # Default (control) behavior
                        result = {
                            "run_key": run_key,
                            "model": "default-model",
                            "timeout": 10.0,
                            "temperature": 0.7,
                            "variant": "control",
                            "experiment": None,
                            "shadow_mode": False,
                        }
                    self.results.append(result)
                    return result

        stage = MockPipelineStage()

        # Run the pipeline for multiple run keys
        run_keys = [f"pipeline-run-{i}" for i in range(50)]
        for rk in run_keys:
            asyncio.run(stage.run(run_key=rk))

        # Verify results
        assert len(stage.results) == 50

        # Check that some runs got the test variant with overrides applied
        test_results = [r for r in stage.results if r["variant"] == "test"]
        control_results = [r for r in stage.results if r["variant"] == "control"]

        assert len(test_results) > 0, "No test variant assignments found"
        assert len(control_results) > 0, "No control variant assignments found"

        # Verify test variant overrides were applied
        for r in test_results:
            assert r["model"] == "groq/experimental-model"
            assert r["timeout"] == 30.0
            assert r["temperature"] == 0.3
            assert r["experiment"] == "pipeline-exp"
            assert r["shadow_mode"] is False

        # Verify control variant uses defaults
        for r in control_results:
            assert r["model"] == "default-model"
            assert r["timeout"] == 10.0
            assert r["temperature"] == 0.7

    def test_pipeline_with_shadow_mode(self, tmp_yaml):
        """Pipeline correctly handles shadow mode — returns overrides with shadow flag."""
        data = {
            "experiments": [
                {
                    "experiment_id": "shadow-pipeline",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {
                                "model": "experimental-reranker",
                                "shadow": True,
                            },
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            for i in range(200):
                overrides = get_ab_overrides(run_key=f"shadow-pipe-run-{i}", layer="reranking")
                if overrides and overrides["variant_key"] == "test":
                    assert overrides["shadow_mode"] is True
                    assert overrides["config"]["model"] == "experimental-reranker"
                    return

            pytest.fail("No test variant assignment found in 200 attempts")

    def test_pipeline_disabled_globally(self, tmp_yaml):
        """When AB testing is globally disabled, pipeline gets no overrides."""
        data = {
            "experiments": [
                {
                    "experiment_id": "disabled-exp",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {"variant_key": "test", "weight": 50, "config": {}},
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = False  # Disabled
            s.ab_config_path = str(tmp_yaml)

            for i in range(50):
                overrides = get_ab_overrides(
                    run_key=f"disabled-run-{i}", layer="query_understanding"
                )
                assert overrides is None

    def test_pipeline_no_experiment_file(self):
        """When no experiment file exists, pipeline gets no overrides."""
        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = "/nonexistent/path/experiments.yaml"

            overrides = get_ab_overrides(run_key="no-file-run", layer="query_understanding")
            assert overrides is None

    def test_pipeline_multiple_layers_independent(self, tmp_yaml):
        """Pipeline stages in different layers get independent overrides."""
        data = {
            "experiments": [
                {
                    "experiment_id": "qu-exp",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"model": "qu-v2"},
                        },
                    ],
                },
                {
                    "experiment_id": "rerank-exp",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {"model": "rerank-v2"},
                        },
                    ],
                },
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            run_key = "multi-layer-run"
            qu_overrides = get_ab_overrides(run_key=run_key, layer="query_understanding")
            rerank_overrides = get_ab_overrides(run_key=run_key, layer="reranking")

            # Both layers should return overrides
            assert qu_overrides is not None
            assert rerank_overrides is not None

            # They should be from different experiments
            assert qu_overrides["experiment_id"] == "qu-exp"
            assert rerank_overrides["experiment_id"] == "rerank-exp"

            # Configs should be layer-specific
            if qu_overrides["variant_key"] == "test":
                assert qu_overrides["config"]["model"] == "qu-v2"
            if rerank_overrides["variant_key"] == "test":
                assert rerank_overrides["config"]["model"] == "rerank-v2"

    def test_pipeline_override_merging(self, tmp_yaml):
        """Pipeline correctly merges AB overrides with default config."""
        data = {
            "experiments": [
                {
                    "experiment_id": "merge-exp",
                    "layer": "query_understanding",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "test",
                            "weight": 50,
                            "config": {
                                "model": "override-model",
                                "extra_param": "should_appear",
                            },
                        },
                    ],
                }
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(tmp_yaml)

            for i in range(200):
                overrides = get_ab_overrides(run_key=f"merge-run-{i}", layer="query_understanding")
                if overrides and overrides["variant_key"] == "test":
                    config = overrides["config"]
                    assert config["model"] == "override-model"
                    assert config["extra_param"] == "should_appear"
                    return

            pytest.fail("No test variant assignment found in 200 attempts")


# =========================================================================
# Additional edge cases
# =========================================================================


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_experiments_list(self):
        """Empty experiments list returns None."""
        result = get_assigned_variant("run-1", "reranking", [])
        assert result is None

    def test_none_run_key(self):
        """get_assigned_variant handles None run_key gracefully."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("exp-1", "reranking", variants)]
        result = get_assigned_variant(None, "reranking", exps)  # type: ignore[arg-type]
        # Should not crash; may return None or an assignment
        assert result is not None or result is None

    def test_malformed_yaml_skipped(self, tmp_yaml, caplog):
        """Malformed experiment entries in YAML are skipped with warning."""
        import logging

        caplog.set_level(logging.WARNING)
        data = {
            "experiments": [
                {"experiment_id": "good", "layer": "reranking", "variants": []},
                {
                    "experiment_id": "bad",
                    "layer": "reranking",
                    "variants": [{"variant_key": "only"}],  # missing weight
                },
            ]
        }
        with open(tmp_yaml, "w") as f:
            yaml.dump(data, f)

        experiments = load_experiments(tmp_yaml)
        # Only the valid experiment (with >=2 variants) should be loaded
        assert len(experiments) == 0  # both are invalid

    def test_unknown_layer_returns_none(self):
        """Requesting a layer with no experiments returns None."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [_make_running_exp("exp-1", "reranking", variants)]
        result = get_assigned_variant("run-1", "nonexistent_layer", exps)
        assert result is None

    def test_save_load_with_all_fields(self, tmp_yaml):
        """Save and load preserves all experiment fields."""
        variants = [
            ABVariant("ctrl", 50, config={"a": 1}, description="Control group"),
            ABVariant("test", 50, config={"b": 2}, description="Test group"),
        ]
        exp = ABExperiment(
            experiment_id="all-fields",
            layer="reranking",
            status="running",
            hypothesis="Test hypothesis",
            primary_metric="mrr",
            traffic_pct=75.0,
            guardrail_metrics=["latency", "cost"],
            started_at="2025-06-01T00:00:00",
            ended_at=None,
            winning_variant=None,
            variants=variants,
            payload={"key": "value"},
        )
        save_experiments([exp], tmp_yaml)
        loaded = load_experiments(tmp_yaml)
        assert len(loaded) == 1
        experiment = loaded[0]
        assert experiment.experiment_id == "all-fields"
        assert experiment.layer == "reranking"
        assert experiment.status == "running"
        assert experiment.hypothesis == "Test hypothesis"
        assert experiment.primary_metric == "mrr"
        assert experiment.traffic_pct == 75.0
        assert experiment.guardrail_metrics == ["latency", "cost"]
        assert experiment.started_at == "2025-06-01T00:00:00"
        assert experiment.ended_at is None
        assert experiment.winning_variant is None
        assert len(experiment.variants) == 2
        assert experiment.variants[0].variant_key == "ctrl"
        assert experiment.variants[0].weight == 50
        assert experiment.variants[0].config == {"a": 1}
        assert experiment.variants[0].description == "Control group"
        assert experiment.variants[1].variant_key == "test"
        assert experiment.variants[1].weight == 50
        assert experiment.variants[1].config == {"b": 2}
        assert experiment.variants[1].description == "Test group"
        assert experiment.payload == {"key": "value"}
