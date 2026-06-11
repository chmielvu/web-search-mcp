from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.ab_testing.assignment import (
    _hash_to_bucket,
    get_assigned_variant,
)
from kindly_web_search_mcp_server.ab_testing.models import (
    ABExperiment,
    ABVariant,
    Assignment,
)
from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
    DEFAULT_CONFIG_PATH,
    load_experiments,
    save_experiments,
)


# ---------------------------------------------------------------------------
# ABVariant tests
# ---------------------------------------------------------------------------


class TestABVariant:
    def test_create_variant_with_minimal_args(self):
        v = ABVariant(variant_key="a", weight=50)
        assert v.variant_key == "a"
        assert v.weight == 50
        assert v.config == {}
        assert v.description == ""

    def test_create_variant_with_all_args(self):
        v = ABVariant(
            variant_key="b",
            weight=100,
            config={"model": "v2"},
            description="experimental model v2",
        )
        assert v.variant_key == "b"
        assert v.weight == 100
        assert v.config == {"model": "v2"}
        assert v.description == "experimental model v2"


# ---------------------------------------------------------------------------
# ABExperiment tests
# ---------------------------------------------------------------------------


class TestABExperiment:
    def test_create_experiment_minimal(self):
        variants = [ABVariant("control", 50), ABVariant("test", 50)]
        exp = ABExperiment(
            experiment_id="exp-1", layer="reranking", variants=variants
        )
        assert exp.experiment_id == "exp-1"
        assert exp.layer == "reranking"
        assert exp.status == "draft"
        assert exp.traffic_pct == 10.0
        assert exp.variants == variants

    def test_validate_valid(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment("exp-1", "reranking", variants=variants)
        assert exp.validate() == []

    def test_validate_missing_experiment_id(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment("", "reranking", variants=variants)
        errors = exp.validate()
        assert "experiment_id is required" in errors

    def test_validate_missing_layer(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment("exp-1", "", variants=variants)
        errors = exp.validate()
        assert "layer is required" in errors

    def test_validate_invalid_status(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment("exp-1", "reranking", status="invalid", variants=variants)
        errors = exp.validate()
        assert any("invalid status" in e for e in errors)

    def test_validate_bad_traffic_pct_zero(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment("exp-1", "reranking", traffic_pct=0, variants=variants)
        errors = exp.validate()
        assert any("traffic_pct" in e for e in errors)

    def test_validate_bad_traffic_pct_over_100(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exp = ABExperiment(
            "exp-1", "reranking", traffic_pct=150, variants=variants
        )
        errors = exp.validate()
        assert any("traffic_pct" in e for e in errors)

    def test_validate_less_than_two_variants(self):
        variants = [ABVariant("only", 100)]
        exp = ABExperiment("exp-1", "reranking", variants=variants)
        errors = exp.validate()
        assert "need at least 2 variants" in errors

    def test_validate_variant_weight_zero(self):
        variants = [ABVariant("ctrl", 0), ABVariant("test", 100)]
        exp = ABExperiment("exp-1", "reranking", variants=variants)
        errors = exp.validate()
        assert any("weight must be > 0" in e for e in errors)

    def test_validate_variant_weight_negative(self):
        variants = [ABVariant("ctrl", -10), ABVariant("test", 100)]
        exp = ABExperiment("exp-1", "reranking", variants=variants)
        errors = exp.validate()
        assert any("weight must be > 0" in e for e in errors)


# ---------------------------------------------------------------------------
# Assignment tests
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_create_assignment(self):
        a = Assignment(
            run_key="run-1",
            experiment_id="exp-1",
            variant_key="control",
            layer="reranking",
        )
        assert a.run_key == "run-1"
        assert a.experiment_id == "exp-1"
        assert a.variant_key == "control"
        assert a.layer == "reranking"
        assert a.shadow_mode is False

    def test_create_assignment_with_shadow_mode(self):
        a = Assignment(
            run_key="run-1",
            experiment_id="exp-1",
            variant_key="test",
            layer="reranking",
            shadow_mode=True,
        )
        assert a.shadow_mode is True


# ---------------------------------------------------------------------------
# _hash_to_bucket tests
# ---------------------------------------------------------------------------


class TestHashToBucket:
    def test_deterministic(self):
        result1 = _hash_to_bucket("run-key-1", "exp-1")
        result2 = _hash_to_bucket("run-key-1", "exp-1")
        assert result1 == result2

    def test_different_run_keys_different_buckets(self):
        b1 = _hash_to_bucket("run-a", "exp-1")
        b2 = _hash_to_bucket("run-b", "exp-1")
        assert b1 != b2  # extremely unlikely to collide

    def test_different_experiments_different_buckets(self):
        b1 = _hash_to_bucket("run-1", "exp-a")
        b2 = _hash_to_bucket("run-1", "exp-b")
        assert b1 != b2

    def test_bucket_range(self):
        for i in range(100):
            bucket = _hash_to_bucket(f"run-{i}", "exp-1")
            assert 0 <= bucket < 10000

    def test_distribution_uniform(self):
        """Rough uniformity check: no single bucket cluster > 5% of 1000 samples."""
        n = 1000
        buckets = [_hash_to_bucket(f"run-{i}", "exp-uniform") for i in range(n)]
        # Group into 10 deciles (0-999, 1000-1999, etc.)
        counts = [0] * 10
        for b in buckets:
            idx = min(b // 1000, 9)
            counts[idx] += 1
        for c in counts:
            pct = c / n * 100
            assert pct < 20, f"Decile count {c}/{n} = {pct}% exceeds 20% threshold"


# ---------------------------------------------------------------------------
# get_assigned_variant tests
# ---------------------------------------------------------------------------


class TestGetAssignedVariant:
    def test_returns_none_when_no_running_experiments(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment(
                "exp-1", "reranking", status="draft", variants=variants
            ),
            ABExperiment(
                "exp-2",
                "reranking",
                status="concluded",
                variants=variants,
            ),
        ]
        result = get_assigned_variant("run-1", "reranking", exps)
        assert result is None

    def test_returns_assignment_for_running_experiment(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment(
                "exp-1",
                "reranking",
                status="running",
                traffic_pct=100,
                variants=variants,
                started_at="2025-01-01",
            ),
        ]
        result = get_assigned_variant("run-1", "reranking", exps)
        assert result is not None
        assert result.experiment_id == "exp-1"
        assert result.variant_key in ("ctrl", "test")
        assert result.layer == "reranking"
        assert result.run_key == "run-1"

    def test_returns_none_when_bucket_exceeds_traffic_pct(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment(
                "exp-1",
                "reranking",
                status="running",
                traffic_pct=0.01,  # only 0.01% traffic
                variants=variants,
                started_at="2025-01-01",
            ),
        ]
        # With 0.01% traffic, nearly all run_keys should be excluded
        results = [
            get_assigned_variant(f"run-{i}", "reranking", exps)
            for i in range(2000)
        ]
        assigned = [r for r in results if r is not None]
        assert len(assigned) < 10  # at most a handful out of 2000

    def test_mutual_exclusion_only_one_running_per_layer(self):
        """When multiple running experiments exist for same layer,
        only the one with the latest started_at should be used."""
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment(
                "exp-old",
                "reranking",
                status="running",
                traffic_pct=100,
                variants=variants,
                started_at="2025-01-01",
            ),
            ABExperiment(
                "exp-new",
                "reranking",
                status="running",
                traffic_pct=100,
                variants=[
                    ABVariant("v2-ctrl", 50),
                    ABVariant("v2-test", 50),
                ],
                started_at="2025-06-01",
            ),
        ]
        result = get_assigned_variant("run-1", "reranking", exps)
        assert result is not None
        assert result.experiment_id == "exp-new"

    def test_different_layers_independent(self):
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        exps = [
            ABExperiment(
                "exp-1",
                "reranking",
                status="running",
                traffic_pct=100,
                variants=variants,
                started_at="2025-01-01",
            ),
        ]
        result = get_assigned_variant("run-1", "query_understanding", exps)
        assert result is None

    def test_variant_selection_respects_weights(self):
        """Heavier variants should be selected more often."""
        variants = [ABVariant("heavy", 90), ABVariant("light", 10)]
        exps = [
            ABExperiment(
                "exp-w",
                "reranking",
                status="running",
                traffic_pct=100,
                variants=variants,
                started_at="2025-01-01",
            ),
        ]
        n = 500
        heavy_count = 0
        for i in range(n):
            r = get_assigned_variant(f"run-{i}", "reranking", exps)
            if r and r.variant_key == "heavy":
                heavy_count += 1
        # heavy should be ~90% — allow some statistical variance
        assert heavy_count > n * 0.5, f"heavy_count={heavy_count}, expected > {n*0.5}"


# ---------------------------------------------------------------------------
# YAML loader tests
# ---------------------------------------------------------------------------


class TestYamlLoader:
    def test_load_experiments_empty_when_no_file(self, tmp_path):
        """Returns empty list when file doesn't exist."""
        result = load_experiments(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_load_experiments_from_yaml(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {
                    "experiment_id": "exp-1",
                    "layer": "reranking",
                    "status": "running",
                    "traffic_pct": 50.0,
                    "hypothesis": "New reranker improves relevance",
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
        with open(config, "w") as f:
            yaml.dump(data, f)

        experiments = load_experiments(config)
        assert len(experiments) == 1
        exp = experiments[0]
        assert exp.experiment_id == "exp-1"
        assert exp.layer == "reranking"
        assert exp.status == "running"
        assert exp.traffic_pct == 50.0
        assert exp.hypothesis == "New reranker improves relevance"
        assert exp.primary_metric == "ndcg@10"
        assert exp.guardrail_metrics == ["latency_p95"]
        assert exp.started_at == "2025-06-01"
        assert len(exp.variants) == 2
        assert exp.variants[0].variant_key == "control"
        assert exp.variants[0].weight == 50
        assert exp.variants[1].variant_key == "test"
        assert exp.variants[1].weight == 50

    def test_load_experiments_skips_invalid(self, tmp_path, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {"experiment_id": "valid", "layer": "reranking", "variants": []},
                {
                    "experiment_id": "valid-2",
                    "layer": "reranking",
                    "variants": [
                        {"variant_key": "a", "weight": 50},
                        {"variant_key": "b", "weight": 50},
                    ],
                },
            ]
        }
        with open(config, "w") as f:
            yaml.dump(data, f)

        experiments = load_experiments(config)
        assert len(experiments) == 1  # only the second one is valid
        assert experiments[0].experiment_id == "valid-2"

    def test_load_experiments_skips_malformed(self, tmp_path, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {
                    "experiment_id": "bad",
                    "layer": "reranking",
                    "variants": [
                        {
                            "variant_key": "a",
                            # missing weight
                        }
                    ],
                },
            ]
        }
        with open(config, "w") as f:
            yaml.dump(data, f)

        experiments = load_experiments(config)
        assert len(experiments) == 0

    def test_save_and_load_round_trip(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        original = [
            ABExperiment(
                experiment_id="exp-rt",
                layer="reranking",
                status="running",
                traffic_pct=30.0,
                hypothesis="Round trip test",
                primary_metric="mrr",
                guardrail_metrics=["latency"],
                started_at="2025-06-01",
                variants=variants,
                payload={"notes": "test payload"},
            )
        ]

        save_experiments(original, config)
        assert config.exists()

        loaded = load_experiments(config)
        assert len(loaded) == 1
        exp = loaded[0]
        assert exp.experiment_id == "exp-rt"
        assert exp.layer == "reranking"
        assert exp.status == "running"
        assert exp.traffic_pct == 30.0
        assert exp.hypothesis == "Round trip test"
        assert exp.primary_metric == "mrr"
        assert exp.guardrail_metrics == ["latency"]
        assert exp.started_at == "2025-06-01"
        assert len(exp.variants) == 2
        assert exp.variants[0].variant_key == "ctrl"
        assert exp.variants[0].weight == 50
        assert exp.variants[1].variant_key == "test"
        assert exp.variants[1].weight == 50
        assert exp.payload == {"notes": "test payload"}

    def test_save_without_payload_omits_payload(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        variants = [ABVariant("ctrl", 50), ABVariant("test", 50)]
        original = [
            ABExperiment(
                experiment_id="exp-nopayload",
                layer="reranking",
                variants=variants,
            )
        ]
        save_experiments(original, config)
        loaded = load_experiments(config)
        assert len(loaded) == 1
        assert loaded[0].payload == {}

    def test_default_config_path(self):
        assert isinstance(DEFAULT_CONFIG_PATH, Path)
        assert DEFAULT_CONFIG_PATH.name == "experiments.yaml"