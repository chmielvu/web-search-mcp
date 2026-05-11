from __future__ import annotations

import asyncio
import os

import pytest

from kindly_web_search_mcp_server.server import get_content

TIMEOUT_URLS = [
    "https://docs.cloud.google.com/batch/docs/troubleshooting",
    "https://discuss.google.dev/t/cloud-batch-suddenly-refusing-to-use-spot-vms/247358",
]


def _can_run_live_tests() -> bool:
    return (
        os.environ.get("KINDLY_RUN_LIVE_TESTS") == "1"
        and os.environ.get("KINDLY_BROWSER_EXECUTABLE_PATH")
    )


@pytest.mark.skipif(
    not _can_run_live_tests(),
    reason="Live fetch tests require KINDLY_RUN_LIVE_TESTS=1 and KINDLY_BROWSER_EXECUTABLE_PATH",
)
def test_get_content_timeout_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("KINDLY_HTML_TOTAL_TIMEOUT_SECONDS", "90")

    for url in TIMEOUT_URLS:
        result = asyncio.run(get_content(url))
        page_content = result.get("page_content", "")
        assert "TimeoutError" not in page_content
