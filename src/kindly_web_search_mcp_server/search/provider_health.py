"""Unified provider health tracker with circuit breaker and per-error-type cooldowns.

Tracks per-provider failures and applies cooldown periods so the search
orchestrator doesn't waste time hitting known-dead providers.

Merges the former standalone CircuitBreaker (circuit_breaker.py) into
ProviderHealthTracker so there is a single source of truth for provider
health state.

Design: in-memory only (no disk persistence). The state resets on server
restart, which is intentional — a fresh start shouldn't inherit stale
cooldown decisions from a prior run.

Cooldown algorithm (normal errors):
- 1st consecutive failure: cooldown 1s
- 2nd: 2s
- 3rd: 4s
- Nth: min(2^(N-1), cooldown_cap) — defaults to 30s cap

Cooldown algorithm (rate limit / HTTP 429):
- 1st: initial_cooldown (default 60s)
- 2nd: initial * 2 = 120s
- 3rd: initial * 4 = 240s
- capped at rate_limit_cap (default 300s)
- Retry-After header is respected when present (capped at 300s)

Circuit breaker state machine (per provider):
- CLOSED: healthy, requests flow normally
- OPEN: cooldown active, requests are blocked
- HALF_OPEN: cooldown expired, one test request allowed
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass

from ..analytics.observability_store import insert_provider_health_transition
from ..telemetry import record_circuit_breaker_event, record_circuit_breaker_state
from ..utils.observability import emit_observability_event, get_current_run_key

logger = logging.getLogger(__name__)


def _emit_provider_health_event(event: str, **fields: object) -> None:
    emit_observability_event(
        logger,
        event,
        level=logging.DEBUG,
        **fields,
    )


def _emit_provider_health_event_async(event: str, **fields: object) -> None:
    thread = threading.Thread(
        target=_emit_provider_health_event,
        args=(event,),
        kwargs=fields,
        daemon=True,
    )
    thread.start()


@dataclass
class _ProviderState:
    """Per-provider health state with circuit breaker."""

    consecutive_failures: int = 0
    last_failure_time: float = 0.0  # monotonic
    cooldown_until: float = 0.0  # monotonic, 0 = no active cooldown
    total_failures: int = 0
    total_successes: int = 0
    # Circuit breaker fields
    circuit_state: str = "closed"  # "closed" | "open" | "half_open"
    opened_at: float = 0.0  # monotonic time when circuit opened
    last_error_type: str | None = None


class ProviderHealthTracker:
    """Unified provider health tracker with circuit breaker.

    Thread-safe for async use (single event loop, no locks needed).
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_cap_seconds: float = 30.0,
        rate_limit_initial_cooldown: float = 60.0,
        rate_limit_cap_seconds: float = 300.0,
    ) -> None:
        self._states: dict[str, _ProviderState] = {}
        self._failure_threshold = failure_threshold
        self._cooldown_cap = cooldown_cap_seconds
        self._rl_initial = rate_limit_initial_cooldown
        self._rl_cap = rate_limit_cap_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mark_success(self, provider_name: str) -> None:
        """Record a successful provider call — resets circuit to closed."""
        state = self._get_or_create(provider_name)
        was_open_or_half = state.circuit_state != "closed"

        state.consecutive_failures = 0
        state.cooldown_until = 0.0
        state.total_successes += 1
        state.circuit_state = "closed"
        state.opened_at = 0.0
        state.last_error_type = None

        if was_open_or_half:
            record_circuit_breaker_event(
                provider_name, "reset", self._failure_threshold
            )
            logger.info(
                "provider_health: %s circuit RESET after success", provider_name
            )
        record_circuit_breaker_state(provider_name, "closed", 0)

        logger.debug(
            "provider_health: %s marked success (total_ok=%d, total_fail=%d)",
            provider_name,
            state.total_successes,
            state.total_failures,
        )
        try:
            insert_provider_health_transition(
                provider=provider_name,
                transition="reset" if was_open_or_half else "success",
                run_key=get_current_run_key(),
                status="closed",
                consecutive_failures=state.consecutive_failures,
                cooldown_seconds=0.0,
                cooldown_remaining_s=0.0,
                total_successes=state.total_successes,
                total_failures=state.total_failures,
                error_type=None,
                is_rate_limit=False,
                circuit_state="closed",
                payload_json={
                    "was_open_or_half": was_open_or_half,
                },
            )
        except Exception as exc:
            logger.debug("provider_health transition insert failed: %s", exc)
        _emit_provider_health_event_async(
            "provider.health.success",
            provider=provider_name,
            consecutive_failures=state.consecutive_failures,
            total_successes=state.total_successes,
            total_failures=state.total_failures,
            cooldown_remaining_s=0.0,
            circuit_state="closed",
        )

    def mark_failure(self, provider_name: str) -> None:
        """Record a failed provider call (backward-compat — treats as generic error)."""
        self.mark_failure_with_type(provider_name, error_type=None)

    def mark_failure_with_type(
        self,
        provider_name: str,
        error_type: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Record a failed provider call with error classification.

        Args:
            provider_name: Provider identifier.
            error_type: e.g. "rate_limit", "TimeoutException", "ConnectError".
            retry_after_seconds: If set (from Retry-After header), used as cooldown.
        """
        state = self._get_or_create(provider_name)
        now = time.monotonic()
        state.consecutive_failures += 1
        state.last_failure_time = now
        state.total_failures += 1
        state.last_error_type = error_type

        is_rate_limit = error_type == "rate_limit"

        # Compute cooldown
        if retry_after_seconds is not None:
            cooldown_s = min(retry_after_seconds, self._rl_cap)
        else:
            cooldown_s = self._cooldown_seconds(
                state.consecutive_failures, is_rate_limit=is_rate_limit
            )

        state.cooldown_until = now + cooldown_s

        # Circuit breaker state transition
        if state.consecutive_failures >= self._failure_threshold:
            state.circuit_state = "open"
            state.opened_at = now
            record_circuit_breaker_state(
                provider_name, "open", state.consecutive_failures
            )
            record_circuit_breaker_event(
                provider_name, "trip", self._failure_threshold
            )
            logger.warning(
                "provider_health: %s circuit OPEN after %d failures (cooldown %.1fs)",
                provider_name,
                state.consecutive_failures,
                cooldown_s,
            )
        else:
            record_circuit_breaker_state(
                provider_name, "closed", state.consecutive_failures
            )

        logger.warning(
            "provider_health: %s failure #%d (%s) — cooldown %.1fs until %s",
            provider_name,
            state.consecutive_failures,
            error_type or "generic",
            cooldown_s,
            time.strftime("%H:%M:%S", time.localtime(time.time() + cooldown_s)),
        )
        try:
            insert_provider_health_transition(
                provider=provider_name,
                transition="open" if state.consecutive_failures >= self._failure_threshold else "cooldown",
                run_key=get_current_run_key(),
                status="open" if state.consecutive_failures >= self._failure_threshold else "closed",
                consecutive_failures=state.consecutive_failures,
                cooldown_seconds=round(cooldown_s, 3),
                cooldown_remaining_s=round(max(0.0, state.cooldown_until - now), 3),
                total_successes=state.total_successes,
                total_failures=state.total_failures,
                error_type=error_type or "generic",
                is_rate_limit=is_rate_limit,
                circuit_state=state.circuit_state,
                payload_json={
                    "retry_after_seconds": retry_after_seconds,
                    "last_failure_time": state.last_failure_time,
                },
            )
        except Exception as exc:
            logger.debug("provider_health transition insert failed: %s", exc)
        _emit_provider_health_event_async(
            "provider.health.cooldown",
            provider=provider_name,
            consecutive_failures=state.consecutive_failures,
            cooldown_seconds=round(cooldown_s, 3),
            cooldown_until=state.cooldown_until,
            total_failures=state.total_failures,
            total_successes=state.total_successes,
            error_type=error_type or "generic",
            circuit_state=state.circuit_state,
            is_rate_limit=is_rate_limit,
        )

    def is_healthy(self, provider_name: str) -> bool:
        """Check if a provider can accept requests.

        Returns False if the provider is in an open circuit (cooldown active).
        When the cooldown expires, transitions to half_open and allows one
        test request through.
        """
        state = self._states.get(provider_name)
        if state is None:
            return True  # Never seen = assumed healthy

        if state.circuit_state == "closed":
            return True

        if state.circuit_state == "open":
            if time.monotonic() >= state.cooldown_until:
                # Cooldown expired — transition to half_open
                state.circuit_state = "half_open"
                record_circuit_breaker_state(
                    provider_name, "half_open", state.consecutive_failures
                )
                record_circuit_breaker_event(
                    provider_name, "half_open", self._failure_threshold
                )
                logger.info(
                    "provider_health: %s circuit HALF_OPEN (cooldown expired)",
                    provider_name,
                )
                return True  # Allow one test request
            return False  # Still in cooldown

        # half_open — allow one test request
        return True

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
                "circuit_state": "closed",
                "last_error_type": None,
            }
        return {
            "provider": provider_name,
            "consecutive_failures": state.consecutive_failures,
            "cooldown_remaining_s": round(self.cooldown_remaining(provider_name), 1),
            "total_failures": state.total_failures,
            "total_successes": state.total_successes,
            "circuit_state": state.circuit_state,
            "last_error_type": state.last_error_type,
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
            _emit_provider_health_event_async(
                "provider.health.reset",
                provider=provider_name,
                scope="single",
            )
        else:
            self._states.clear()
            logger.info("provider_health: reset all providers")
            _emit_provider_health_event_async(
                "provider.health.reset",
                provider="all",
                scope="all",
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, provider_name: str) -> _ProviderState:
        if provider_name not in self._states:
            self._states[provider_name] = _ProviderState()
        return self._states[provider_name]

    def _cooldown_seconds(
        self, consecutive_failures: int, *, is_rate_limit: bool = False
    ) -> float:
        """Exponential backoff with error-type-specific parameters."""
        if is_rate_limit:
            # Rate limits: start at 60s, double each time, cap at 300s
            base = self._rl_initial * (2 ** (consecutive_failures - 1))
            return min(base, self._rl_cap)
        # Normal errors: start at 1s, double each time, cap at 30s
        base = 1 << (consecutive_failures - 1)  # 1, 2, 4, 8, 16, 32…
        return min(float(base), self._cooldown_cap)


# ------------------------------------------------------------------
# Module-level singleton (lazy init in server.py)
# ------------------------------------------------------------------
_provider_health: ProviderHealthTracker | None = None


def get_provider_health() -> ProviderHealthTracker:
    """Get or create the singleton ProviderHealthTracker."""
    global _provider_health
    if _provider_health is None:
        from ..settings import settings

        _provider_health = ProviderHealthTracker(
            failure_threshold=settings.provider_failure_threshold,
            cooldown_cap_seconds=settings.provider_cooldown_cap_seconds,
            rate_limit_initial_cooldown=settings.provider_rate_limit_initial_cooldown,
            rate_limit_cap_seconds=settings.provider_rate_limit_cap_seconds,
        )
    return _provider_health


def set_provider_health(tracker: ProviderHealthTracker) -> None:
    """Set the singleton (for testing)."""
    global _provider_health
    _provider_health = tracker


def reset_provider_health() -> None:
    """Reset the singleton (for testing)."""
    global _provider_health
    _provider_health = None
