from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestComposioClient(unittest.TestCase):
    def test_require_composio_config_reads_live_env(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "COMPOSIO_API_KEY": "api-key",
                    "COMPOSIO_USER_ID": "user-id",
                },
                clear=True,
            ),
            patch(
                "kindly_web_search_mcp_server.composio_client.settings.composio_api_key",
                "",
            ),
            patch(
                "kindly_web_search_mcp_server.composio_client.settings.composio_user_id",
                "",
            ),
        ):
            from kindly_web_search_mcp_server.composio_client import (
                _require_composio_config,
            )

            self.assertEqual(_require_composio_config(), ("api-key", "user-id"))


if __name__ == "__main__":
    unittest.main()
