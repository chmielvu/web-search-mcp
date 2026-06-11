"""Tests for A/B testing settings in Settings dataclass."""

import importlib
import os

import kindly_web_search_mcp_server.settings as settings_module
from kindly_web_search_mcp_server.settings import Settings


class TestABSettingsDefaults:
    """Verify the new A/B testing fields exist with correct defaults."""

    def setup_method(self) -> None:
        # Clear relevant env vars so defaults are used
        for key in [
            "AB_TESTING_ENABLED",
            "AB_CONFIG_PATH",
            "AB_SHADOW_MODE_DEFAULT",
            "AB_ASSIGNMENT_CACHE_TTL_SECONDS",
        ]:
            os.environ.pop(key, None)

    def test_ab_testing_enabled_defaults_to_false(self) -> None:
        s = Settings()
        assert s.ab_testing_enabled is False

    def test_ab_config_path_default(self) -> None:
        s = Settings()
        # Path uses OS separators; check for the directory structure
        assert "duckdb_data" in s.ab_config_path
        assert "experiments" in s.ab_config_path
        assert s.ab_config_path.endswith("experiments.yaml")

    def test_ab_shadow_mode_default_defaults_to_true(self) -> None:
        s = Settings()
        assert s.ab_shadow_mode_default is True

    def test_ab_assignment_cache_ttl_seconds_default(self) -> None:
        s = Settings()
        assert s.ab_assignment_cache_ttl_seconds == 300


class TestABSettingsEnvOverride:
    """Verify the new fields can be overridden via environment variables.

    Note: because dataclass field defaults with os.environ.get() are evaluated
    at class definition time, we must reload the module after setting env vars.
    """

    def _reload_settings(self) -> type[Settings]:
        """Reload the settings module to re-evaluate field defaults."""
        importlib.reload(settings_module)
        return settings_module.Settings

    def teardown_method(self) -> None:
        # Restore the module to its original state for other tests
        importlib.reload(settings_module)

    def test_ab_testing_enabled_via_env(self) -> None:
        os.environ["AB_TESTING_ENABLED"] = "true"
        SettingsCls = self._reload_settings()
        s = SettingsCls()
        assert s.ab_testing_enabled is True

    def test_ab_config_path_via_env(self) -> None:
        os.environ["AB_CONFIG_PATH"] = "/custom/path/experiments.yaml"
        SettingsCls = self._reload_settings()
        s = SettingsCls()
        assert s.ab_config_path == "/custom/path/experiments.yaml"

    def test_ab_shadow_mode_default_via_env(self) -> None:
        os.environ["AB_SHADOW_MODE_DEFAULT"] = "false"
        SettingsCls = self._reload_settings()
        s = SettingsCls()
        assert s.ab_shadow_mode_default is False

    def test_ab_assignment_cache_ttl_seconds_via_env(self) -> None:
        os.environ["AB_ASSIGNMENT_CACHE_TTL_SECONDS"] = "600"
        SettingsCls = self._reload_settings()
        s = SettingsCls()
        assert s.ab_assignment_cache_ttl_seconds == 600
