"""Deterministic state-machine + concurrency tests for HFCircuitBreaker.

Covered:
- closed -> open after FAILURE_THRESHOLD consecutive failures
- open -> half_open after RECOVERY_TIMEOUT_SECONDS elapsed (driven by stub)
- half_open permits exactly one probe
- half_open probe success -> closed and probe claim released
- half_open probe failure -> open and probe claim released
- get_state / get_failure_count are read under the breaker lock
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from kindly_web_search_mcp_server.embeddings.hf_inference import HFCircuitBreaker


class _Clock:
    """Monotonic clock stub the breaker can be driven with."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_breaker(clock: _Clock) -> HFCircuitBreaker:
    breaker = HFCircuitBreaker()
    breaker._lock = threading.Lock()  # noqa: SLF001 -- already exists; re-init harmless
    # Replace private time access for deterministic tests.
    breaker._time = clock  # type: ignore[attr-defined]
    # Patch is_open/record_* closures via monkey patching time.time used there.
    import kindly_web_search_mcp_server.embeddings.hf_inference as hf

    original_time = hf.time.time
    hf.time.time = clock  # type: ignore[assignment]
    breaker._restore_time = lambda: setattr(hf.time, "time", original_time)  # type: ignore[attr-defined]
    return breaker


def teardown_breaker(breaker: HFCircuitBreaker) -> None:
    restore = getattr(breaker, "_restore_time", None)
    if restore is not None:
        restore()


def test_closed_to_open_after_threshold():
    clock = _Clock()
    breaker = _make_breaker(clock)
    try:
        assert breaker.get_state() == "closed"
        assert not breaker.is_open()

        breaker.record_failure()
        breaker.record_failure()
        assert breaker.get_state() == "closed"
        assert not breaker.is_open()

        breaker.record_failure()
        assert breaker.get_state() == "open"
        assert breaker.is_open()
    finally:
        teardown_breaker(breaker)


def test_open_to_half_open_after_recovery_timeout_and_single_probe():
    clock = _Clock()
    breaker = _make_breaker(clock)
    try:
        for _ in range(HFCircuitBreaker.FAILURE_THRESHOLD):
            breaker.record_failure()
        assert breaker.get_state() == "open"

        # Still inside the recovery window: blocked.
        assert breaker.is_open()
        assert breaker.get_state() == "open"

        # Past the recovery window: transitions to half_open and yields the probe.
        clock.advance(HFCircuitBreaker.RECOVERY_TIMEOUT_SECONDS + 1)
        assert not breaker.is_open()  # probe released to this caller
        assert breaker.get_state() == "half_open"

        # The next call sees the probe already claimed and is blocked.
        assert breaker.is_open()
        assert breaker.get_state() == "half_open"
    finally:
        teardown_breaker(breaker)


def test_half_open_probe_success_returns_to_closed():
    clock = _Clock()
    breaker = _make_breaker(clock)
    try:
        for _ in range(HFCircuitBreaker.FAILURE_THRESHOLD):
            breaker.record_failure()
        clock.advance(HFCircuitBreaker.RECOVERY_TIMEOUT_SECONDS + 1)
        assert not breaker.is_open()  # probe claimed
        breaker.record_success()
        assert breaker.get_state() == "closed"
        assert not breaker.is_open()
        assert breaker.get_failure_count() == 0
    finally:
        teardown_breaker(breaker)


def test_half_open_probe_failure_returns_to_open():
    clock = _Clock()
    breaker = _make_breaker(clock)
    try:
        for _ in range(HFCircuitBreaker.FAILURE_THRESHOLD):
            breaker.record_failure()
        clock.advance(HFCircuitBreaker.RECOVERY_TIMEOUT_SECONDS + 1)
        assert not breaker.is_open()  # probe claimed
        breaker.record_failure()
        assert breaker.get_state() == "open"
        # Probe claim must be cleared so the next half_open cycle permits a probe.
        clock.advance(HFCircuitBreaker.RECOVERY_TIMEOUT_SECONDS + 1)
        assert not breaker.is_open()
    finally:
        teardown_breaker(breaker)


def test_concurrent_is_open_releases_exactly_one_probe():
    clock = _Clock()
    breaker = _make_breaker(clock)
    try:
        for _ in range(HFCircuitBreaker.FAILURE_THRESHOLD):
            breaker.record_failure()
        clock.advance(HFCircuitBreaker.RECOVERY_TIMEOUT_SECONDS + 1)

        results: list[bool] = []
        start = threading.Barrier(8)

        def probe() -> None:
            start.wait()
            results.append(breaker.is_open())

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(probe) for _ in range(8)]
            for f in futures:
                f.result(timeout=10)

        # Exactly one caller gets the probe; the rest are blocked.
        assert results.count(False) == 1, results
        assert results.count(True) == 7, results
    finally:
        teardown_breaker(breaker)
