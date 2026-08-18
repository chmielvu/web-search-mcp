"""Provider resilience for academic search.

Tracks per-provider throttling, consecutive 429 counts, consecutive failure
counts, and consecutive zero-result runs so the academic search orchestrator
can pace polite API usage, disable providers after repeated 429s/failures, and
skip providers that keep returning zero results for the remainder of a run.

Inspired by agentpub/agentpub.org ``academic_search.py``. All methods are
synchronous except :meth:`ProviderResilience.throttle`; thread-safety is not
required (single event loop, matching the orchestrator's module-level
singleton usage).
"""

from __future__ import annotations

import asyncio
import time

# Canonical provider -> minimum seconds between successive calls.
DEFAULT_MIN_INTERVALS: dict[str, float] = {
    "arxiv": 3.0,
    "semanticscholar": 2.0,
    "openalex": 0.5,
    "crossref": 1.0,
    "pubmed": 0.34,
    "core": 2.0,
    "radon": 1.0,
    "bn": 1.0,
    "pbn": 1.0,
    "polona": 1.0,
    "dlibra": 2.0,
    "rds": 1.0,
    "europeana": 1.0,
}

# Interval applied to providers not present in the configured map.
DEFAULT_PROVIDER_INTERVAL: float = 1.0

# Consecutive 429s before the provider is disabled for the run.
DISABLE_AFTER_429S: int = 3
# Consecutive generic failures before the provider is disabled for the run.
DISABLE_AFTER_FAILURES: int = 3
# Consecutive zero-result queries before the caller skips the provider.
SKIP_AFTER_ZERO_RESULTS: int = 3


class ProviderResilience:
    """Per-provider throttling and failure state for academic search.

    Attributes are tracked lazily: only providers that are actually used appear
    in the internal counters, while :meth:`snapshot` always reports every
    configured provider (plus any extra providers that recorded state) so
    observability shows a stable per-provider view.
    """

    def __init__(self, min_intervals: dict[str, float] | None = None) -> None:
        self._min_intervals: dict[str, float] = dict(DEFAULT_MIN_INTERVALS)
        if min_intervals:
            self._min_intervals.update(min_intervals)
        self._last_call: dict[str, float] = {}
        self._consecutive_429: dict[str, int] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._consecutive_zero: dict[str, int] = {}
        self._disabled: set[str] = set()

    async def throttle(self, provider: str) -> None:
        """Wait until the provider's minimum interval has elapsed since the
        last call, then record the call. Unknown providers use the default
        interval of 1.0 and never raise.
        """
        interval = self._min_intervals.get(provider, DEFAULT_PROVIDER_INTERVAL)
        last = self._last_call.get(provider)
        if last is not None:
            remaining = interval - (time.monotonic() - last)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_call[provider] = time.monotonic()

    def record_429(self, provider: str) -> bool:
        """Record a rate-limit (429) response. Returns True once the provider
        has been disabled after 3 consecutive 429s; False otherwise.
        """
        count = self._consecutive_429.get(provider, 0) + 1
        self._consecutive_429[provider] = count
        if count >= DISABLE_AFTER_429S:
            self._disabled.add(provider)
            return True
        return False

    def record_failure(self, provider: str) -> bool:
        """Record a generic failure. Returns True once the provider has been
        disabled after 3 consecutive failures; False otherwise.
        """
        count = self._consecutive_failures.get(provider, 0) + 1
        self._consecutive_failures[provider] = count
        if count >= DISABLE_AFTER_FAILURES:
            self._disabled.add(provider)
            return True
        return False

    def record_zero_results(self, provider: str) -> bool:
        """Record a zero-result query. Returns True after 3 consecutive
        zero-result queries so the caller can skip the provider for the
        remaining queries in this run (does not disable it).
        """
        count = self._consecutive_zero.get(provider, 0) + 1
        self._consecutive_zero[provider] = count
        return count >= SKIP_AFTER_ZERO_RESULTS

    def record_success(self, provider: str, result_count: int) -> None:
        """Record a successful call. Resets the consecutive 429 and failure
        counters; resets the zero-result counter only when results arrived.
        """
        self._consecutive_429.pop(provider, None)
        self._consecutive_failures.pop(provider, None)
        if result_count > 0:
            self._consecutive_zero.pop(provider, None)

    def is_disabled(self, provider: str) -> bool:
        """True once the provider has been disabled by repeated 429s/failures."""
        return provider in self._disabled

    def snapshot(self) -> dict[str, dict[str, bool | int]]:
        """Per-provider state for observability: disabled flag and consecutive
        429 / failure / zero-result counts for every configured provider.
        """
        providers = (
            set(self._min_intervals)
            | set(self._consecutive_429)
            | set(self._consecutive_failures)
            | set(self._consecutive_zero)
            | set(self._disabled)
        )
        return {
            provider: {
                "disabled": provider in self._disabled,
                "consecutive_429": self._consecutive_429.get(provider, 0),
                "consecutive_failures": self._consecutive_failures.get(provider, 0),
                "consecutive_zero": self._consecutive_zero.get(provider, 0),
            }
            for provider in sorted(providers)
        }
