"""Provider health tracker with exponential backoff cooldown.

Tracks per-provider failures and applies cooldown periods so the search
orchestrator doesn't waste time hitting known-dead providers.

Design: in-memory only (no disk persistence). The state resets on server
restart, which is intentional — a fresh start shouldn't inherit stale
cooldown decisions from a prior run.

Cooldown algorithm:
- 1st consecutive failure: cooldown 1s
- 2nd: 2s
- 3rd: 4s
- 4th: 8s
- 5th+: 30s (cap)
- First success after cooldown: reset consecutive_failures to 0
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _ProviderState:
    """Per-provider health state."""

    consecutive_failures: int = 0
    last_failure_time: float = 0.0  # monotonic
    cooldown_until: float = 0.0  # monotonic, 0 = no active cooldown
    total_failures: int = 0
    total_successes: int = 0


class ProviderHealthTracker:
    """Tracks health of search providers with exponential backoff cooldown.

    Thread-safe for async use (single event loop, no locks needed).
    """

    def __init__(self) -> None:
        self._states: dict[str, _ProviderState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mark_success(self, provider_name: str) -> None:
        """Record a successful provider call."""
        state = self._get_or_create(provider_name)
        state.consecutive_failures = 0
        state.cooldown_until = 0.0
        state.total_successes += 1
        logger.debug(
            "provider_health: %s marked success (total_ok=%d, total_fail=%d)",
            provider_name,
            state.total_successes,
            state.total_failures,
        )

    def mark_failure(self, provider_name: str) -> None:
        """Record a failed provider call and compute cooldown."""
        state = self._get_or_create(provider_name)
        now = time.monotonic()
        state.consecutive_failures += 1
        state.last_failure_time = now
        state.total_failures += 1

        cooldown_s = self._cooldown_seconds(state.consecutive_failures)
        state.cooldown_until = now + cooldown_s
        logger.warning(
            "provider_health: %s failure #%d — cooldown %.1fs until %s",
            provider_name,
            state.consecutive_failures,
            cooldown_s,
            time.strftime("%H:%M:%S", time.localtime(time.time() + cooldown_s)),
        )

    def is_healthy(self, provider_name: str) -> bool:
        """Return False if this provider is in cooldown."""
        state = self._states.get(provider_name)
        if state is None:
            return True  # Never seen = assumed healthy
        if state.cooldown_until == 0.0:
            return True
        if time.monotonic() >= state.cooldown_until:
            # Cooldown expired — provider can be retried
            # (state remains until success or another failure)
            return True
        return False

    def cooldown_remaining(self, provider_name: str) -> float:
        """Return seconds remaining in cooldown, or 0.0."""
        state = self._states.get(provider_name)
        if state is None or state.cooldown_until == 0.0:
            return 0.0
        remaining = state.cooldown_until - time.monotonic()
        return max(0.0, remaining)

    def get_state(self, provider_name: str) -> dict:
        """Return a snapshot of provider health for diagnostics/resources."""
        state = self._states.get(provider_name)
        if state is None:
            return {
                "provider": provider_name,
                "consecutive_failures": 0,
                "cooldown_remaining_s": 0.0,
                "total_failures": 0,
                "total_successes": 0,
            }
        return {
            "provider": provider_name,
            "consecutive_failures": state.consecutive_failures,
            "cooldown_remaining_s": round(self.cooldown_remaining(provider_name), 1),
            "total_failures": state.total_failures,
            "total_successes": state.total_successes,
        }

    def all_states(self) -> list[dict]:
        """Return health snapshots for all tracked providers."""
        return [self.get_state(name) for name in sorted(self._states)]

    def reset(self, provider_name: str | None = None) -> None:
        """Reset health state for one provider or all.

        Args:
            provider_name: If given, reset only this provider.
                If None, reset all providers.
        """
        if provider_name is not None:
            self._states.pop(provider_name, None)
            logger.info("provider_health: reset %s", provider_name)
        else:
            self._states.clear()
            logger.info("provider_health: reset all providers")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, provider_name: str) -> _ProviderState:
        if provider_name not in self._states:
            self._states[provider_name] = _ProviderState()
        return self._states[provider_name]

    @staticmethod
    def _cooldown_seconds(consecutive_failures: int) -> float:
        """Exponential backoff capped at 30s."""
        base = 1 << (consecutive_failures - 1)  # 1, 2, 4, 8, 16, 32…
        return min(float(base), 30.0)


# ------------------------------------------------------------------
# Module-level singleton (lazy init in server.py)
# ------------------------------------------------------------------
_provider_health: ProviderHealthTracker | None = None


def get_provider_health() -> ProviderHealthTracker:
    """Get or create the singleton ProviderHealthTracker."""
    global _provider_health
    if _provider_health is None:
        _provider_health = ProviderHealthTracker()
    return _provider_health


def set_provider_health(tracker: ProviderHealthTracker) -> None:
    """Set the singleton (for testing)."""
    global _provider_health
    _provider_health = tracker


def reset_provider_health() -> None:
    """Reset the singleton (for testing)."""
    global _provider_health
    _provider_health = None
