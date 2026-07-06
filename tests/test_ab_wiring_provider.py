"""Tests for A/B testing wiring into provider weights (Task 25)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.ab_testing.wiring import get_ab_overrides


class TestProviderWeightsABOverrides:
    """Tests that provider weight A/B overrides work correctly."""

    def test_returns_none_when_disabled(self):
        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = False
            result = get_ab_overrides(run_key="run-1", layer="provider_weights")
            assert result is None

    def test_returns_override_when_enrolled(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {
                    "experiment_id": "pw-exp-1",
                    "layer": "provider_weights",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "heavy-searxng",
                            "weight": 50,
                            "config": {
                                "provider_weights": {"searxng": 2.0, "tavily": 0.5},
                            },
                        },
                    ],
                }
            ]
        }
        with open(config, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(config)

            # Try enough run keys to hit one enrolled in the test variant
            result = None
            for i in range(100):
                result = get_ab_overrides(run_key=f"run-{i}", layer="provider_weights")
                if result and result["variant_key"] == "heavy-searxng":
                    break

        assert result is not None
        assert result["experiment_id"] == "pw-exp-1"
        if result["variant_key"] == "heavy-searxng":
            assert result["config"]["provider_weights"]["searxng"] == 2.0
            assert result["config"]["provider_weights"]["tavily"] == 0.5

    def test_shadow_mode_variant(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {
                    "experiment_id": "pw-shadow",
                    "layer": "provider_weights",
                    "status": "running",
                    "traffic_pct": 100.0,
                    "started_at": "2025-06-01",
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {
                            "variant_key": "shadow-test",
                            "weight": 50,
                            "config": {
                                "provider_weights": {"searxng": 3.0},
                                "shadow": True,
                            },
                        },
                    ],
                }
            ]
        }
        with open(config, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(config)

            for i in range(200):
                result = get_ab_overrides(run_key=f"run-{i}", layer="provider_weights")
                if result and result["variant_key"] == "shadow-test":
                    assert result["shadow_mode"] is True
                    assert result["config"]["provider_weights"]["searxng"] == 3.0
                    return

        pytest.fail("No shadow-test variant found in 200 attempts")

    def test_no_running_experiment_returns_none(self, tmp_path):
        config = tmp_path / "experiments.yaml"
        data = {
            "experiments": [
                {
                    "experiment_id": "pw-draft",
                    "layer": "provider_weights",
                    "status": "draft",
                    "traffic_pct": 100.0,
                    "variants": [
                        {"variant_key": "control", "weight": 50, "config": {}},
                        {"variant_key": "test", "weight": 50, "config": {}},
                    ],
                }
            ]
        }
        with open(config, "w") as f:
            yaml.dump(data, f)

        with patch("kindly_web_search_mcp_server.ab_testing.wiring.settings") as s:
            s.ab_testing_enabled = True
            s.ab_config_path = str(config)
            result = get_ab_overrides(run_key="run-1", layer="provider_weights")
            assert result is None
