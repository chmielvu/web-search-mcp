"""Deprecated: Chromium browser pool management.

Replaced by Crawl4AI/Playwright which manages its own browser lifecycle.
This module is kept to avoid import breakage during transition.
"""

from __future__ import annotations

from ..utils.diagnostics import Diagnostics


class _DeprecatedPool:
    """Stub that raises on use."""


async def get_chromium_pool(diagnostics: Diagnostics | None = None) -> _DeprecatedPool:
    raise RuntimeError(
        "ChromiumPool is deprecated. Crawl4AI manages browser lifecycle internally. "
        "Remove any code that calls get_chromium_pool()."
    )


def reuse_enabled() -> bool:
    """Deprecated: Crawl4AI manages its own browser. Pool is no longer used."""
    return False
