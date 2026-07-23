"""Telemetry recording helpers for circuit breakers."""

from __future__ import annotations

from .attributes import (
    CIRCUIT_EVENT,
    CIRCUIT_FAILURE_THRESHOLD,
    PROVIDER_NAME,
)
from .metrics import get_circuit_metrics, update_circuit_state


def record_circuit_breaker_state(
    provider: str,
    state: str,
    failure_count: int,
) -> None:
    """Record circuit breaker state for a provider.

    Args:
        provider: Provider name
        state: "closed", "open", or "half_open"
        failure_count: Consecutive failures
    """
    state_value = 0.0 if state == "closed" else (1.0 if state == "open" else 0.5)
    update_circuit_state(provider, state, state_value, failure_count)


def record_circuit_breaker_event(
    provider: str,
    event: str,
    failure_threshold: int = 3,
) -> None:
    """Record circuit breaker state change event.

    Args:
        provider: Provider name
        event: "trip", "reset", or "half_open"
        failure_threshold: Threshold that triggered the event
    """
    _, event_counter = get_circuit_metrics()
    event_counter.add(
        1,
        {
            PROVIDER_NAME: provider,
            CIRCUIT_EVENT: event,
            CIRCUIT_FAILURE_THRESHOLD: failure_threshold,
        },
    )


__all__ = [
    "record_circuit_breaker_event",
    "record_circuit_breaker_state",
]
