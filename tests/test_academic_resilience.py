"""Unit tests for the academic search ProviderResilience module.

Covers per-provider throttle pacing, consecutive-429/zero-result/failure
state, success resets, the observability snapshot, and the default-interval
behavior for unknown providers.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from kindly_web_search_mcp_server.search.academic.provider_resilience import (
    DEFAULT_PROVIDER_INTERVAL,
    ProviderResilience,
)


# ---------------------------------------------------------------------------
# (a) throttle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_waits_between_quick_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call within the interval sleeps for the remaining time."""
    slept: list[float] = []

    async def fake_sleep(duration: float) -> None:
        slept.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    resilience = ProviderResilience()

    await resilience.throttle("arxiv")
    assert slept == []  # first call never sleeps

    await resilience.throttle("arxiv")  # immediately after: must sleep ~3.0s
    assert len(slept) == 1
    assert slept[0] == pytest.approx(3.0, abs=0.01)


@pytest.mark.asyncio
async def test_throttle_does_not_sleep_after_interval_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call after the interval has elapsed does not sleep again."""
    slept: list[float] = []

    async def fake_sleep(duration: float) -> None:
        slept.append(duration)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    resilience = ProviderResilience()

    await resilience.throttle("openalex")  # interval 0.5
    await real_sleep(0.6)  # real time > 0.5s interval
    await resilience.throttle("openalex")

    assert slept == []


@pytest.mark.asyncio
async def test_throttle_real_time_elapsed_at_least_interval() -> None:
    """End-to-end timing: two quick calls to a 0.2s provider take >= 0.2s."""
    resilience = ProviderResilience({"probe": 0.2})

    t0 = time.monotonic()
    await resilience.throttle("probe")
    await resilience.throttle("probe")
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.2 - 0.01
    assert elapsed < 2.0  # sanity: did not hang


# ---------------------------------------------------------------------------
# (b) record_429 / record_success
# ---------------------------------------------------------------------------


def test_record_429_disables_after_three_and_success_resets() -> None:
    resilience = ProviderResilience()

    assert resilience.record_429("arxiv") is False
    assert resilience.record_429("arxiv") is False
    assert resilience.record_429("arxiv") is True  # third 429 disables
    assert resilience.is_disabled("arxiv") is True

    # record_success resets the 429 counter; the disabled flag is sticky
    # (no re-enable path exists in the API).
    resilience.record_success("arxiv", 5)
    assert resilience.snapshot()["arxiv"]["consecutive_429"] == 0
    assert resilience.is_disabled("arxiv") is True


def test_record_429_success_before_threshold_resets_counter() -> None:
    resilience = ProviderResilience()
    resilience.record_429("crossref")
    resilience.record_429("crossref")
    resilience.record_success("crossref", 1)

    assert resilience.record_429("crossref") is False  # back to count 1
    assert resilience.is_disabled("crossref") is False


# ---------------------------------------------------------------------------
# (c) record_zero_results
# ---------------------------------------------------------------------------


def test_zero_results_skip_after_three() -> None:
    resilience = ProviderResilience()

    assert resilience.record_zero_results("semanticscholar") is False
    assert resilience.record_zero_results("semanticscholar") is False
    assert resilience.record_zero_results("semanticscholar") is True
    # Zero-result skip is not a disable: the provider stays enabled.
    assert resilience.is_disabled("semanticscholar") is False


def test_zero_results_success_with_results_resets() -> None:
    resilience = ProviderResilience()
    resilience.record_zero_results("core")
    resilience.record_zero_results("core")
    resilience.record_zero_results("core")
    assert resilience.record_zero_results("core") is True

    resilience.record_success("core", 2)  # results arrived: reset
    assert resilience.record_zero_results("core") is False


def test_zero_results_success_with_zero_results_does_not_reset() -> None:
    """The orchestrator calls record_success(name, 0) before re-recording a
    zero-result outcome, so a 0-count success must not clear the streak."""
    resilience = ProviderResilience()
    resilience.record_zero_results("pubmed")
    resilience.record_zero_results("pubmed")

    resilience.record_success("pubmed", 0)
    assert resilience.record_zero_results("pubmed") is True  # third in a row


# ---------------------------------------------------------------------------
# (d) record_failure
# ---------------------------------------------------------------------------


def test_record_failure_disables_after_three() -> None:
    resilience = ProviderResilience()

    assert resilience.record_failure("openalex") is False
    assert resilience.record_failure("openalex") is False
    assert resilience.record_failure("openalex") is True
    assert resilience.is_disabled("openalex") is True

    resilience.record_success("openalex", 3)
    assert resilience.snapshot()["openalex"]["consecutive_failures"] == 0


def test_429_and_failure_counters_are_independent() -> None:
    """Each counter must hit its own limit before disabling."""
    resilience = ProviderResilience()
    resilience.record_429("radon")
    resilience.record_failure("radon")
    resilience.record_failure("radon")
    # 1 429 + 2 failures: neither counter reached its limit of 3.
    assert resilience.is_disabled("radon") is False

    resilience.record_failure("radon")
    assert resilience.is_disabled("radon") is True


# ---------------------------------------------------------------------------
# (e) snapshot
# ---------------------------------------------------------------------------


def test_snapshot_reflects_state() -> None:
    resilience = ProviderResilience()
    resilience.record_429("arxiv")
    resilience.record_429("arxiv")
    resilience.record_failure("openalex")
    resilience.record_zero_results("crossref")
    resilience.record_zero_results("crossref")

    snap = resilience.snapshot()
    assert snap["arxiv"] == {
        "disabled": False,
        "consecutive_429": 2,
        "consecutive_failures": 0,
        "consecutive_zero": 0,
    }
    assert snap["openalex"] == {
        "disabled": False,
        "consecutive_429": 0,
        "consecutive_failures": 1,
        "consecutive_zero": 0,
    }
    assert snap["crossref"] == {
        "disabled": False,
        "consecutive_429": 0,
        "consecutive_failures": 0,
        "consecutive_zero": 2,
    }
    # Untouched default providers appear with zeroed state.
    assert snap["dlibra"] == {
        "disabled": False,
        "consecutive_429": 0,
        "consecutive_failures": 0,
        "consecutive_zero": 0,
    }


def test_snapshot_marks_disabled_providers() -> None:
    resilience = ProviderResilience()
    for _ in range(3):
        resilience.record_failure("polona")

    assert resilience.snapshot()["polona"]["disabled"] is True


# ---------------------------------------------------------------------------
# (f) unknown providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_provider_uses_default_interval_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(duration: float) -> None:
        slept.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    resilience = ProviderResilience()

    await resilience.throttle("made-up-provider")  # must not raise
    assert slept == []

    await resilience.throttle("made-up-provider")  # quick second call
    assert len(slept) == 1
    assert slept[0] == pytest.approx(DEFAULT_PROVIDER_INTERVAL, abs=0.01)


@pytest.mark.asyncio
async def test_unknown_provider_default_interval_survives_custom_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(duration: float) -> None:
        slept.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    resilience = ProviderResilience({"arxiv": 0.01})  # custom override only

    await resilience.throttle("made-up-provider")
    await resilience.throttle("made-up-provider")
    assert len(slept) == 1
    assert slept[0] == pytest.approx(DEFAULT_PROVIDER_INTERVAL, abs=0.01)

    # Custom intervals override defaults for known providers.
    await resilience.throttle("arxiv")
    await resilience.throttle("arxiv")
    assert slept[-1] == pytest.approx(0.01, abs=0.01)
