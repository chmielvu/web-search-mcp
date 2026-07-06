"""Daily quota tracker for YouTube Data API v3.

Tracks unit consumption in memory with day-rollover reset.
No persistence needed — quota resets daily at midnight Pacific time.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Quota warning thresholds
_WARN_THRESHOLD = 0.80  # 80%
_HALT_THRESHOLD = 1.00  # 100%


class YouTubeApiQuotaTracker:
    """In-memory daily quota tracker for YouTube Data API v3.

    Thread-safe. Resets when the UTC date changes.
    Default daily quota: 10,000 units (Google's default).
    """

    def __init__(self, daily_quota: int = 10_000) -> None:
        self._daily_quota = daily_quota
        self._lock = threading.Lock()
        self._today: str = ""
        self._used: int = 0
        self._call_count: int = 0
        self._failures: int = 0

    def _maybe_rollover(self) -> None:
        """Reset counters if the UTC date has changed."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._today:
            if self._today:
                logger.info(
                    "YouTube API quota rollover: %s → %s (used %d/%d units)",
                    self._today,
                    today,
                    self._used,
                    self._daily_quota,
                )
            self._today = today
            self._used = 0
            self._call_count = 0
            self._failures = 0

    def can_afford(self, units: int) -> bool:
        """Check whether the requested units fit within the daily quota."""
        with self._lock:
            self._maybe_rollover()
            return (self._used + units) <= self._daily_quota

    def record_call(self, success: bool, units: int) -> None:
        """Record a quota-consuming API call."""
        with self._lock:
            self._maybe_rollover()
            self._used += units
            self._call_count += 1
            if not success:
                self._failures += 1

            usage_ratio = self._used / self._daily_quota if self._daily_quota else 0

            if usage_ratio >= _HALT_THRESHOLD:
                logger.warning(
                    "YouTube API daily quota EXHAUSTED: %d/%d units used (%d calls, %d failures)",
                    self._used,
                    self._daily_quota,
                    self._call_count,
                    self._failures,
                )
            elif usage_ratio >= _WARN_THRESHOLD:
                logger.warning(
                    "YouTube API daily quota at %.0f%%: %d/%d units used",
                    usage_ratio * 100,
                    self._used,
                    self._daily_quota,
                )

    def snapshot(self) -> dict[str, Any]:
        """Return current quota state for diagnostics."""
        with self._lock:
            self._maybe_rollover()
            return {
                "date": self._today,
                "daily_quota": self._daily_quota,
                "used": self._used,
                "remaining": max(0, self._daily_quota - self._used),
                "usage_pct": round(self._used / self._daily_quota * 100, 1)
                if self._daily_quota
                else 0,
                "call_count": self._call_count,
                "failures": self._failures,
            }


# Module-level singleton
_quota_tracker: YouTubeApiQuotaTracker | None = None
_tracker_lock = threading.Lock()


def get_youtube_api_quota_tracker() -> YouTubeApiQuotaTracker:
    """Return the singleton quota tracker."""
    global _quota_tracker
    if _quota_tracker is None:
        with _tracker_lock:
            if _quota_tracker is None:
                from ..settings import settings

                _quota_tracker = YouTubeApiQuotaTracker(
                    daily_quota=settings.youtube_api_daily_quota,
                )
    return _quota_tracker
