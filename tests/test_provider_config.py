"""Tests for provider configuration and mode-based selection."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.provider_config import (
    ProviderConfig,
    ProviderMode,
    parse_provider_mode,
)


class TestProviderMode(unittest.TestCase):
    """Test ProviderMode enum values."""

    def test_mode_values(self) -> None:
        self.assertEqual(ProviderMode.ALWAYS.value, "always")
        self.assertEqual(ProviderMode.CONDITIONAL.value, "conditional")
        self.assertEqual(ProviderMode.NEVER.value, "never")


class TestParseProviderMode(unittest.TestCase):
    """Test parsing provider mode from environment values."""

    def test_parse_always(self) -> None:
        result = parse_provider_mode("always")
        self.assertEqual(result, ProviderMode.ALWAYS)

    def test_parse_conditional(self) -> None:
        result = parse_provider_mode("conditional")
        self.assertEqual(result, ProviderMode.CONDITIONAL)

    def test_parse_never(self) -> None:
        result = parse_provider_mode("never")
        self.assertEqual(result, ProviderMode.NEVER)

    def test_parse_case_insensitive(self) -> None:
        self.assertEqual(parse_provider_mode("ALWAYS"), ProviderMode.ALWAYS)
        self.assertEqual(parse_provider_mode("Conditional"), ProviderMode.CONDITIONAL)
        self.assertEqual(parse_provider_mode("NEVER"), ProviderMode.NEVER)

    def test_parse_whitespace(self) -> None:
        self.assertEqual(parse_provider_mode("  always  "), ProviderMode.ALWAYS)

    def test_parse_invalid(self) -> None:
        self.assertIsNone(parse_provider_mode("invalid"))
        self.assertIsNone(parse_provider_mode(""))
        self.assertIsNone(parse_provider_mode("enabled"))


class TestProviderConfig(unittest.TestCase):
    """Test ProviderConfig class."""

    def test_provider_mode_always(self) -> None:
        # Free provider with no env key requirement (like DDG)
        config = ProviderConfig(
            name="ddg",
            mode=ProviderMode.ALWAYS,
            env_key="",  # Empty env key for free providers
            search_fn=lambda: [],
            is_free=True,
            requires_key=False,
        )
        # Always mode should return True for should_fire when available (no caller list)
        self.assertTrue(config.should_fire())
        self.assertTrue(config.should_fire(caller_providers=None))

        # When caller specifies explicit providers, acts as allow-list.
        # "ddg" not in ["other"] -> should NOT fire
        self.assertFalse(config.should_fire(caller_providers=["other"]))
        # "ddg" in ["ddg", "tavily"] -> should fire
        self.assertTrue(config.should_fire(caller_providers=["ddg", "tavily"]))

    def test_provider_mode_never(self) -> None:
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.NEVER,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        # Never mode should always return False
        self.assertFalse(config.should_fire())
        self.assertFalse(config.should_fire(caller_providers=None))
        self.assertFalse(config.should_fire(caller_providers=["test"]))

    def test_provider_mode_conditional_without_caller(self) -> None:
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.CONDITIONAL,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        # Conditional without caller request: False
        self.assertFalse(config.should_fire(caller_providers=None))
        self.assertFalse(config.should_fire(caller_providers=["other"]))

    def test_provider_mode_conditional_with_caller_no_key(self) -> None:
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.CONDITIONAL,
            env_key="TEST_KEY",  # Not set in env
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        # With caller request but no key: False (not available)
        self.assertFalse(config.should_fire(caller_providers=["test"]))

    def test_provider_mode_conditional_with_caller_and_key(self) -> None:
        os.environ["TEST_KEY"] = "test-value"
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.CONDITIONAL,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        # With caller request AND key: True
        self.assertTrue(config.should_fire(caller_providers=["test"]))
        # Without caller request: False
        self.assertFalse(config.should_fire(caller_providers=None))
        os.environ.pop("TEST_KEY", None)

    def test_provider_is_available_no_env_key(self) -> None:
        # DDG has no env key requirement
        config = ProviderConfig(
            name="ddg",
            mode=ProviderMode.ALWAYS,
            env_key="",  # Empty env key
            search_fn=lambda: [],
            is_free=True,
            requires_key=False,
        )
        self.assertTrue(config.is_available())

    def test_provider_is_available_with_key(self) -> None:
        os.environ["TEST_KEY"] = "value"
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.ALWAYS,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        self.assertTrue(config.is_available())
        os.environ.pop("TEST_KEY", None)

    def test_provider_is_available_requires_extra_env_keys(self) -> None:
        os.environ["TEST_KEY"] = "value"
        os.environ.pop("EXTRA_TEST_KEY", None)
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.ALWAYS,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
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
            mode=ProviderMode.ALWAYS,
            env_key="MISSING_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        self.assertFalse(config.is_available())

    def test_provider_never_mode_not_available(self) -> None:
        os.environ["TEST_KEY"] = "value"
        config = ProviderConfig(
            name="test",
            mode=ProviderMode.NEVER,
            env_key="TEST_KEY",
            search_fn=lambda: [],
            is_free=False,
            requires_key=True,
        )
        # NEVER mode should report not available regardless of key
        self.assertFalse(config.is_available())
        os.environ.pop("TEST_KEY", None)

    def test_always_provider_respects_allow_list(self) -> None:
        """Test that explicit caller_providers acts as allow-list for ALWAYS providers."""
        # Simulate SearXNG (ALWAYS mode, free)
        searxng = ProviderConfig(
            name="searxng",
            mode=ProviderMode.ALWAYS,
            env_key="SEARXNG_BASE_URL",
            search_fn=lambda: [],
            is_free=True,
            requires_key=False,
        )
        os.environ["SEARXNG_BASE_URL"] = "http://localhost:8080"

        # Simulate DDG (ALWAYS mode, free, no env key)
        ddg = ProviderConfig(
            name="ddg",
            mode=ProviderMode.ALWAYS,
            env_key="",  # No env key needed
            search_fn=lambda: [],
            is_free=True,
            requires_key=False,
        )

        # When caller requests ONLY composio, ALWAYS providers should NOT fire
        self.assertFalse(searxng.should_fire(caller_providers=["composio_llm_search"]))
        self.assertFalse(ddg.should_fire(caller_providers=["composio_llm_search"]))

        # When caller requests ONLY jina, ALWAYS providers should NOT fire
        self.assertFalse(searxng.should_fire(caller_providers=["jina"]))
        self.assertFalse(ddg.should_fire(caller_providers=["jina"]))

        # When caller explicitly includes searxng, it should fire
        self.assertTrue(searxng.should_fire(caller_providers=["searxng"]))
        self.assertTrue(searxng.should_fire(caller_providers=["searxng", "tavily"]))

        # When caller explicitly includes ddg, it should fire
        self.assertTrue(ddg.should_fire(caller_providers=["ddg"]))
        self.assertTrue(ddg.should_fire(caller_providers=["ddg", "searxng"]))

        # Empty caller list should NOT fire any provider (empty allow-list)
        self.assertFalse(searxng.should_fire(caller_providers=[]))
        self.assertFalse(ddg.should_fire(caller_providers=[]))

        # No caller list specified -> use mode-based (ALWAYS fires)
        self.assertTrue(searxng.should_fire(caller_providers=None))
        self.assertTrue(ddg.should_fire(caller_providers=None))

        os.environ.pop("SEARXNG_BASE_URL", None)


if __name__ == "__main__":
    unittest.main()
