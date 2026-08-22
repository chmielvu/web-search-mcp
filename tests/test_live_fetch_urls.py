from __future__ import annotations

import asyncio
import os

import pytest


TIMEOUT_URLS = [
    "https://docs.cloud.google.com/batch/docs/troubleshooting",
    "https://discuss.google.dev/t/cloud-batch-suddenly-refusing-to-use-spot-vms/247358",
]


def _can_run_live_tests() -> bool:
    return os.environ.get("RUN_LIVE_TESTS") == "1" and os.environ.get("BROWSER_EXECUTABLE_PATH")


@pytest.mark.skipif(
    not _can_run_live_tests(),
    reason="Live fetch tests require RUN_LIVE_TESTS=1 and BROWSER_EXECUTABLE_PATH",
)
def test_fetch_timeout_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    from kindly_web_search_mcp_server.server import fetch

    monkeypatch.setenv("KINDLY_WEB_FETCH_TIMEOUT_SECONDS", "20")

    for url in TIMEOUT_URLS:
        result = asyncio.run(fetch(url=url))
        page_content = result.results[0].page_content
        assert "TimeoutError" not in page_content
