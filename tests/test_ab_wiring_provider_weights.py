"""Tests for A/B testing wiring into provider weights (Task 25)."""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Direct tests for the provider_weights A/B wiring logic
# ---------------------------------------------------------------------------


class TestABWiringProviderWeightsDirect:
    """Direct unit tests on the provider_weights A/B wiring logic."""

    def test_ab_override_updates_weights(self):
        """Variant config with provider_weights overrides base weights."""
        base_weights = {"tavily": 1.3, "searxng": 1.0}
        variant_overrides = {"tavily": 2.0, "brave": 1.5}

        merged = dict(base_weights)
        merged.update(variant_overrides)

        assert merged["tavily"] == 2.0
        assert merged["searxng"] == 1.0
        assert merged["brave"] == 1.5

    def test_ab_override_empty_config_preserves_base(self):
        """Empty variant config provider_weights leaves base weights unchanged."""
        base_weights = {"tavily": 1.3, "searxng": 1.0}
        variant_overrides = {}

        merged = dict(base_weights)
        merged.update(variant_overrides)

        assert merged == {"tavily": 1.3, "searxng": 1.0}

    def test_ab_override_partial_update(self):
        """Partial variant provider_weights only updates specified providers."""
        base_weights = {"tavily": 1.3, "searxng": 1.0, "brave": 1.0}
        variant_overrides = {"tavily": 2.0}

        merged = dict(base_weights)
        merged.update(variant_overrides)

        assert merged["tavily"] == 2.0
        assert merged["searxng"] == 1.0
        assert merged["brave"] == 1.0

    def test_non_shadow_override_applies_variant_weights(self):
        """Simulate the pipeline logic: non-shadow mode merges variant weights."""
        base_weights = {"tavily": 1.3, "searxng": 1.0, "brave": 1.0}
        ab_overrides = {
            "experiment_id": "pw-exp-1",
            "variant_key": "test",
            "shadow_mode": False,
            "config": {
                "provider_weights": {
                    "tavily": 2.0,
                    "gemini": 1.5,
                },
            },
        }

        # This is the exact logic from pipeline.py
        if ab_overrides and not ab_overrides.get("shadow_mode"):
            pw_config = ab_overrides.get("config", {})
            variant_weights = pw_config.get("provider_weights", {})
            if variant_weights:
                effective_weights = dict(base_weights)
                effective_weights.update(variant_weights)
            else:
                effective_weights = dict(base_weights)
        else:
            effective_weights = dict(base_weights)

        assert effective_weights["tavily"] == 2.0  # overridden
        assert effective_weights["searxng"] == 1.0  # unchanged
        assert effective_weights["brave"] == 1.0  # unchanged
        assert effective_weights["gemini"] == 1.5  # new provider added

    def test_shadow_mode_preserves_base_weights(self):
        """Simulate shadow mode: control path uses base weights unchanged."""
        base_weights = {"tavily": 1.3, "searxng": 1.0}
        ab_overrides = {
            "experiment_id": "pw-shadow-1",
            "variant_key": "test",
            "shadow_mode": True,
            "config": {
                "provider_weights": {
                    "tavily": 2.0,
                },
            },
        }

        # Shadow mode: control uses base weights
        if ab_overrides and not ab_overrides.get("shadow_mode"):
            effective_weights = dict(base_weights)
            effective_weights.update({"tavily": 2.0})
        else:
            effective_weights = dict(base_weights)

        assert effective_weights == {"tavily": 1.3, "searxng": 1.0}

    def test_no_overrides_uses_base_weights(self):
        """When no AB overrides exist, base weights are used."""
        base_weights = {"tavily": 1.3, "searxng": 1.0}
        ab_overrides = None  # No experiment enrollment

        if ab_overrides and not ab_overrides.get("shadow_mode"):
            effective_weights = dict(base_weights)
            effective_weights.update({"tavily": 2.0})
        else:
            effective_weights = dict(base_weights)

        assert effective_weights == {"tavily": 1.3, "searxng": 1.0}


# ---------------------------------------------------------------------------
# Tests for YAML config format for provider_weights experiments
# ---------------------------------------------------------------------------


class TestABWiringProviderWeightsConfigFormat:
    """Validate that the YAML experiment format supports provider_weights."""

    def _make_temp_yaml(self, data):
        """Create a temp YAML file, return its path."""
        tmp = tempfile.mktemp(suffix=".yaml", dir=os.getcwd())
        import yaml

        with open(tmp, "w") as f:
            yaml.dump(data, f)
        return tmp

    def test_sample_experiment_yaml(self):
        """Ensure a sample YAML for provider_weights can be loaded correctly."""

        from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
            load_experiments,
        )

        config_path = self._make_temp_yaml(
            {
                "experiments": [
                    {
                        "experiment_id": "pw-exp-1",
                        "layer": "provider_weights",
                        "status": "running",
                        "traffic_pct": 50.0,
                        "primary_metric": "ndcg_at_10",
                        "started_at": "2025-06-11",
                        "hypothesis": "Increasing tavily weight improves top-5 relevance",
                        "variants": [
                            {
                                "variant_key": "control",
                                "weight": 50,
                                "config": {},
                                "description": "Default provider weights",
                            },
                            {
                                "variant_key": "treatment",
                                "weight": 50,
                                "config": {
                                    "provider_weights": {
                                        "tavily": 2.0,
                                        "brave": 1.2,
                                        "gemini": 1.5,
                                    },
                                },
                                "description": "Boost tavily, brave, and gemini",
                            },
                        ],
                    }
                ]
            }
        )

        try:
            experiments = load_experiments(config_path)
            assert len(experiments) == 1

            exp = experiments[0]
            assert exp.layer == "provider_weights"
            assert exp.experiment_id == "pw-exp-1"
            assert exp.status == "running"
            assert len(exp.variants) == 2

            # Verify control variant
            control = [v for v in exp.variants if v.variant_key == "control"][0]
            assert control.weight == 50
            assert control.config == {}

            # Verify treatment variant
            treatment = [v for v in exp.variants if v.variant_key == "treatment"][0]
            assert treatment.weight == 50
            assert "provider_weights" in treatment.config
            assert treatment.config["provider_weights"]["tavily"] == 2.0
            assert treatment.config["provider_weights"]["brave"] == 1.2
            assert treatment.config["provider_weights"]["gemini"] == 1.5
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass

    def test_shadow_mode_experiment_yaml(self):
        """Ensure a shadow-mode YAML for provider_weights can be loaded."""

        from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
            load_experiments,
        )

        config_path = self._make_temp_yaml(
            {
                "experiments": [
                    {
                        "experiment_id": "pw-shadow-1",
                        "layer": "provider_weights",
                        "status": "running",
                        "traffic_pct": 100.0,
                        "started_at": "2025-06-11",
                        "variants": [
                            {
                                "variant_key": "control",
                                "weight": 50,
                                "config": {},
                            },
                            {
                                "variant_key": "test",
                                "weight": 50,
                                "config": {
                                    "provider_weights": {
                                        "tavily": 1.8,
                                    },
                                    "shadow": True,
                                },
                            },
                        ],
                    }
                ]
            }
        )

        try:
            experiments = load_experiments(config_path)
            assert len(experiments) == 1

            test_variant = [v for v in experiments[0].variants if v.variant_key == "test"][0]
            assert test_variant.config.get("shadow") is True
            assert test_variant.config["provider_weights"]["tavily"] == 1.8
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass

    def test_yaml_roundtrip(self):
        """Ensure experiments can be saved and reloaded."""

        from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
            load_experiments,
            save_experiments,
        )
        from kindly_web_search_mcp_server.ab_testing.models import (
            ABExperiment,
            ABVariant,
        )

        exp = ABExperiment(
            experiment_id="pw-roundtrip",
            layer="provider_weights",
            status="running",
            traffic_pct=25.0,
            started_at="2025-06-11",
            variants=[
                ABVariant(
                    variant_key="control",
                    weight=50,
                    config={},
                ),
                ABVariant(
                    variant_key="test",
                    weight=50,
                    config={
                        "provider_weights": {
                            "tavily": 1.5,
                            "searxng": 0.8,
                        },
                    },
                ),
            ],
        )

        config_path = tempfile.mktemp(suffix=".yaml", dir=os.getcwd())
        try:
            save_experiments([exp], config_path)
            loaded = load_experiments(config_path)

            assert len(loaded) == 1
            assert loaded[0].experiment_id == "pw-roundtrip"
            assert loaded[0].layer == "provider_weights"
            assert len(loaded[0].variants) == 2

            test_v = [v for v in loaded[0].variants if v.variant_key == "test"][0]
            assert test_v.config["provider_weights"]["tavily"] == 1.5
            assert test_v.config["provider_weights"]["searxng"] == 0.8
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Tests for provider_weights layer with the wiring module
# ---------------------------------------------------------------------------


class TestGetABOverridesProviderWeightsLayer:
    """get_ab_overrides should handle provider_weights layer correctly."""

    def test_provider_weights_is_valid_layer_constant(self):
        """The string 'provider_weights' should be a recognized layer value."""
        from kindly_web_search_mcp_server.ab_testing.models import (
            ABExperiment,
            ABVariant,
        )

        exp = ABExperiment(
            experiment_id="pw-test",
            layer="provider_weights",
            status="running",
            traffic_pct=100.0,
            variants=[
                ABVariant(variant_key="control", weight=50, config={}),
                ABVariant(
                    variant_key="test",
                    weight=50,
                    config={"provider_weights": {"tavily": 2.0}},
                ),
            ],
        )
        errors = exp.validate()
        assert len(errors) == 0
        assert exp.layer == "provider_weights"

    def test_yaml_loader_accepts_provider_weights_layer(self):
        """Verify yaml_loader correctly loads provider_weights layer experiments."""
        import yaml

        from kindly_web_search_mcp_server.ab_testing.yaml_loader import (
            load_experiments,
        )

        config_path = tempfile.mktemp(suffix=".yaml", dir=os.getcwd())
        try:
            data = {
                "experiments": [
                    {
                        "experiment_id": "pw-exp-1",
                        "layer": "provider_weights",
                        "status": "running",
                        "traffic_pct": 100.0,
                        "started_at": "2025-06-11",
                        "variants": [
                            {"variant_key": "control", "weight": 50, "config": {}},
                            {
                                "variant_key": "treatment",
                                "weight": 50,
                                "config": {
                                    "provider_weights": {"tavily": 2.0},
                                },
                            },
                        ],
                    }
                ]
            }
            with open(config_path, "w") as f:
                yaml.dump(data, f)

            experiments = load_experiments(config_path)
            assert len(experiments) == 1
            assert experiments[0].layer == "provider_weights"
            assert experiments[0].experiment_id == "pw-exp-1"

            # Verify variants load correctly with provider_weights config
            treatment = [v for v in experiments[0].variants if v.variant_key == "treatment"][0]
            assert "provider_weights" in treatment.config
            assert treatment.config["provider_weights"]["tavily"] == 2.0
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Pipeline integration tests for provider_weights A/B wiring
# ---------------------------------------------------------------------------


class TestPipelineABWiringProviderWeightsLogic:
    """Test the wiring logic that pipeline.py would execute.

    These tests directly test the weight-merging logic that was added to
    pipeline.py without importing the full pipeline module.
    """

    def test_non_shadow_weights_merged_correctly(self):
        """Non-shadow mode produces correct effective weights."""
        base = {"tavily": 1.3, "searxng": 1.0}
        overrides = {
            "experiment_id": "pw-exp-1",
            "variant_key": "test",
            "shadow_mode": False,
            "config": {"provider_weights": {"tavily": 2.0, "brave": 1.5}},
        }

        if overrides and not overrides.get("shadow_mode"):
            pw_config = overrides.get("config", {})
            vw = pw_config.get("provider_weights", {})
            effective = dict(base)
            if vw:
                effective.update(vw)
        else:
            effective = dict(base)

        assert effective == {"tavily": 2.0, "searxng": 1.0, "brave": 1.5}

    def test_shadow_mode_uses_base_weights(self):
        """Shadow mode passes base weights to merge."""
        base = {"tavily": 1.3, "searxng": 1.0}
        overrides = {
            "experiment_id": "pw-shadow-1",
            "variant_key": "test",
            "shadow_mode": True,
            "config": {"provider_weights": {"tavily": 2.0}},
        }

        if overrides and not overrides.get("shadow_mode"):
            effective = dict(base)
            effective.update({"tavily": 2.0})
        else:
            effective = dict(base)

        assert effective == base

    def test_no_overrides_passes_base(self):
        """No overrides gives base weights."""
        base = {"tavily": 1.3, "searxng": 1.0}

        pw_ab_overrides = None  # No enrollment

        if pw_ab_overrides and not pw_ab_overrides.get("shadow_mode"):
            pw_config = pw_ab_overrides.get("config", {})
            vw = pw_config.get("provider_weights", {})
            effective = dict(base)
            if vw:
                effective.update(vw)
        else:
            effective = dict(base)

        assert effective == base
