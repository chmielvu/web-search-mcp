from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.provider_config import (
    PROVIDER_REGISTRY,
    ProviderConfig,
    ProviderGroup,
    register_provider,
    resolve_provider_configs,
    select_paid_serp_configs,
)
from kindly_web_search_mcp_server.settings import settings


class TestProviderConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._providers_enabled = settings.providers_enabled
        self._disabled_providers = settings.disabled_providers
        self._qdrant_search_enabled = settings.qdrant_search_enabled
        self._registry_snapshot = PROVIDER_REGISTRY.copy()

    def tearDown(self) -> None:
        settings.providers_enabled = self._providers_enabled
        settings.disabled_providers = self._disabled_providers
        settings.qdrant_search_enabled = self._qdrant_search_enabled
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(self._registry_snapshot)

    def test_provider_is_available_no_env_key(self) -> None:
        config = ProviderConfig(
            name="ddg",
            env_key="",
            search_fn=lambda: [],
            group=ProviderGroup.free,
            requires_key=False,
        )
        self.assertTrue(config.is_available())

    def test_provider_is_enabled_blocks_master_switch(self) -> None:
        settings.providers_enabled = False
        config = ProviderConfig(
            name="unit_test_provider",
            env_key="",
            search_fn=lambda: [],
            group=ProviderGroup.specialized,
            requires_key=False,
        )
        self.assertFalse(config.is_enabled())

    def test_provider_is_enabled_blocks_qdrant_search_flag(self) -> None:
        settings.qdrant_search_enabled = False
        config = ProviderConfig(
            name="qdrant",
            env_key="QDRANT_SPACE_URL",
            search_fn=lambda: [],
            group=ProviderGroup.free,
            requires_key=False,
        )
        self.assertFalse(config.is_enabled())

    def test_resolve_provider_configs_respects_disabled_provider_list(self) -> None:
        os.environ["TEST_KEY"] = "value"
        settings.disabled_providers = ("unit_test_provider",)
        config = ProviderConfig(
            name="unit_test_provider",
            env_key="TEST_KEY",
            search_fn=lambda: [],
            group=ProviderGroup.specialized,
            requires_key=True,
        )
        register_provider(config)

        active = resolve_provider_configs(["unit_test_provider"])

        self.assertEqual(active, [])
        os.environ.pop("TEST_KEY", None)

    def test_select_paid_serp_configs_uses_shared_round_robin(self) -> None:
        configs = [
            ProviderConfig(
                name="brave",
                env_key="",
                search_fn=lambda: [],
                group=ProviderGroup.paid_serp,
                requires_key=False,
            ),
            ProviderConfig(
                name="serpapi",
                env_key="",
                search_fn=lambda: [],
                group=ProviderGroup.paid_serp,
                requires_key=False,
            ),
            ProviderConfig(
                name="serper",
                env_key="",
                search_fn=lambda: [],
                group=ProviderGroup.paid_serp,
                requires_key=False,
            ),
        ]

        first = [config.name for config in select_paid_serp_configs(configs, limit=2)]
        second = [config.name for config in select_paid_serp_configs(configs, limit=2)]

        self.assertEqual(first, ["brave", "serpapi"])
        self.assertEqual(second, ["serper", "brave"])


if __name__ == "__main__":
    unittest.main()
