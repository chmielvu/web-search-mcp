from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Redirect pytest's temp root to a project-local directory before pytest
# imports its tmpdir machinery. The default root
# (C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>) may be owned by a
# different Windows user from a prior session, in which case pytest's
# `getbasetemp()` raises PermissionError [WinError 5] at fixture setup for
# every test that uses tmp_path. Setting this env var points pytest at a
# fresh, current-user-owned directory so the 28 collection errors collapse
# without changing production code or skipping any test. `.pytest-tmp/` is
# already in `.gitignore`.
_pytest_root = (Path(__file__).resolve().parents[1] / ".pytest-tmp").resolve()
_pytest_root.mkdir(exist_ok=True)
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(_pytest_root))


@pytest.fixture(scope="session", autouse=True)
def sandbox_cwd():
    """
    Run the whole session from a throwaway sandbox directory.

    Many analytics tests create cwd-relative DuckDB files (``test_*.duckdb``
    via ``Path(self._testMethodName)`` or ``Path("test_...")``). Without this,
    every run leaks those files into the repo root (7+ MB seen 2026-08-20).
    The sandbox lives under the project-local ``.pytest-tmp`` root already
    configured above, so it is gitignored and user-owned.
    """
    sandbox = _pytest_root / "cwd-sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    os.chdir(sandbox)
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture(scope="session", autouse=True)
def patch_settings():
    """
    Patch settings for the test session.
    """
    # Set test provider keys if not provided by the environment.
    # This keeps unit tests deterministic while allowing opt-in live integration tests.
    os.environ.setdefault("SEARXNG_BASE_URL", "https://searx.example.org")
    os.environ.setdefault("TAVILY_API_KEY", "test_api_key")
