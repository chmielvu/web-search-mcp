"""Soft quota tracking for Google CSE."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date

LOGGER = logging.getLogger(__name__)


@dataclass
class _QuotaState:
    day: str = ""
    calls: int = 0
    successes: int = 0
    failures: int = 0


class GoogleCseQuotaTracker:
    """Track daily Google CSE usage with a soft limit."""

    def __init__(self, soft_daily_limit: int = 100) -> None:
        self.soft_daily_limit = max(1, soft_daily_limit)
        self._state = _QuotaState()
        self._lock = threading.RLock()

    def record_call(self, *, success: bool) -> dict[str, object]:
        with self._lock:
            self._rollover_locked()
            self._state.calls += 1
            if success:
                self._state.successes += 1
            else:
                self._state.failures += 1
            self._log_thresholds_locked()
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._rollover_locked()
            remaining = max(0, self.soft_daily_limit - self._state.calls)
            return {
                "day": self._state.day,
                "calls": self._state.calls,
                "successes": self._state.successes,
                "failures": self._state.failures,
                "soft_daily_limit": self.soft_daily_limit,
                "remaining": remaining,
                "soft_limit_reached": self._state.calls >= self.soft_daily_limit,
            }

    def reset(self) -> None:
        with self._lock:
            self._state = _QuotaState(day=self._today())

    def _rollover_locked(self) -> None:
        today = self._today()
        if self._state.day != today:
            self._state = _QuotaState(day=today)

    def _log_thresholds_locked(self) -> None:
        if self._state.calls == self.soft_daily_limit:
            LOGGER.warning("Google CSE soft daily quota reached (%d calls).", self.soft_daily_limit)
        elif self._state.calls == max(1, int(self.soft_daily_limit * 0.8)):
            LOGGER.info(
                "Google CSE soft daily quota nearing limit (%d/%d calls).",
                self._state.calls,
                self.soft_daily_limit,
            )

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()


_tracker: GoogleCseQuotaTracker | None = None


def get_google_cse_quota_tracker() -> GoogleCseQuotaTracker:
    global _tracker
    if _tracker is None:
        _tracker = GoogleCseQuotaTracker()
    return _tracker
