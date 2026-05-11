from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(scope="session", autouse=True)
def patch_settings():
    """
    Patch settings for the test session.
    """
    # Set test provider keys if not provided by the environment.
    # This keeps unit tests deterministic while allowing opt-in live integration tests.
    os.environ.setdefault("SEARXNG_BASE_URL", "https://searx.example.org")
    os.environ.setdefault("TAVILY_API_KEY", "test_api_key")
    os.environ.setdefault("KINDLY_GEMINI_SEARCH_MODE", "never")
