"""Per-provider circuit breaker. Opens after N consecutive failures."""

import logging
import time
from dataclasses import dataclass, field

from ..telemetry import record_circuit_breaker_event, record_circuit_breaker_state

LOGGER = logging.getLogger(__name__)


@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker. Opens after N consecutive failures."""

    failure_threshold: int = 3
    reset_timeout_seconds: float = 60.0
    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def is_open(self, provider: str) -> bool:
        if provider not in self._opened_at:
            record_circuit_breaker_state(
                provider, "closed", self._failures.get(provider, 0)
            )
            return False
        if time.time() - self._opened_at[provider] > self.reset_timeout_seconds:
            del self._opened_at[provider]
            self._failures[provider] = 0
            record_circuit_breaker_state(provider, "half_open", 0)
            record_circuit_breaker_event(provider, "half_open", self.failure_threshold)
            LOGGER.info(f"Circuit breaker HALF_OPEN for {provider} after reset timeout")
            return False
        record_circuit_breaker_state(provider, "open", self._failures.get(provider, 0))
        return True

    def record_success(self, provider: str) -> None:
        was_open = provider in self._opened_at
        self._failures[provider] = 0
        self._opened_at.pop(provider, None)
        if was_open:
            record_circuit_breaker_event(provider, "reset", self.failure_threshold)
            LOGGER.info(f"Circuit breaker RESET for {provider} after success")
        record_circuit_breaker_state(provider, "closed", 0)

    def record_failure(self, provider: str) -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        failure_count = self._failures[provider]

        if failure_count >= self.failure_threshold:
            self._opened_at[provider] = time.time()
            record_circuit_breaker_state(provider, "open", failure_count)
            record_circuit_breaker_event(provider, "trip", self.failure_threshold)
            LOGGER.warning(
                f"Circuit breaker OPEN for {provider} after {failure_count} failures"
            )
        else:
            record_circuit_breaker_state(provider, "closed", failure_count)
