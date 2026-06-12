"""Tests for provider configuration and allow-list selection."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.provider_config import ProviderConfig, ProviderGroup


class TestProviderConfig(unittest.TestCase):
    """Test ProviderConfig class."""

    def test_provider_fires_when_available_and_unrestricted(self) -> None:
        os.environ["TEST_KEY"] = "value"
        config = ProviderConfig(
            name="test",
            env_key="TEST_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.free,
            requires_key=True,
        )
        self.assertTrue(config.should_fire())
        os.environ.pop("TEST_KEY", None)

    def test_provider_respects_allow_list(self) -> None:
        os.environ["SEARXNG_BASE_URL"] = "http://localhost:8080"
        config = ProviderConfig(
            name="searxng",
            env_key="SEARXNG_BASE_URL",
            search_fn=lambda: [],
            group=ProviderGroup.free,
            requires_key=False,
        )

        self.assertTrue(config.should_fire())

        os.environ.pop("SEARXNG_BASE_URL", None)

    def test_provider_is_available_no_env_key(self) -> None:
        config = ProviderConfig(
            name="ddg",
            env_key="",
            search_fn=lambda: [],
            group=ProviderGroup.free,
            requires_key=False,
        )
        self.assertTrue(config.is_available())

    def test_provider_is_available_with_key(self) -> None:
        os.environ["TEST_KEY"] = "value"
        config = ProviderConfig(
            name="test",
            env_key="TEST_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.other,
            requires_key=True,
        )
        self.assertTrue(config.is_available())
        os.environ.pop("TEST_KEY", None)

    def test_provider_is_available_requires_extra_env_keys(self) -> None:
        os.environ["TEST_KEY"] = "value"
        os.environ.pop("EXTRA_TEST_KEY", None)
        config = ProviderConfig(
            name="test",
            env_key="TEST_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.other,
            requires_key=True,
            extra_env_keys=("EXTRA_TEST_KEY",),
        )
        self.assertFalse(config.is_available())
        os.environ["EXTRA_TEST_KEY"] = "extra"
        self.assertTrue(config.is_available())
        os.environ.pop("TEST_KEY", None)
        os.environ.pop("EXTRA_TEST_KEY", None)

    def test_provider_is_available_without_key(self) -> None:
        config = ProviderConfig(
            name="test",
            env_key="MISSING_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.other,
            requires_key=True,
        )
        self.assertFalse(config.is_available())

    def test_allow_list_empty_blocks_all(self) -> None:
        os.environ["TEST_KEY"] = "value"
        config = ProviderConfig(
            name="test",
            env_key="TEST_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.other,
            requires_key=True,
        )
        self.assertFalse(config.should_fire())
        os.environ.pop("TEST_KEY", None)


if __name__ == "__main__":
    unittest.main()
