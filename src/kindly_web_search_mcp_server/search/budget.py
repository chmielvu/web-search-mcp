"""Tracks per-provider calls and auto-demotion on poor performance."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderBudget:
    """Tracks per-provider calls and auto-demotion on poor performance."""

    max_calls_per_query: int = 3
    stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    _demoted: set[str] = field(default_factory=set)

    def can_spend(self, provider: str) -> bool:
        if provider in self._demoted:
            return False
        s = self.stats.get(provider)
        if s is None:
            return True
        if s["calls"] >= self.max_calls_per_query:
            return False
        if s["calls"] >= 2 and s["failures"] / s["calls"] > 0.5:
            self._demoted.add(provider)
            return False
        return True

    def record_call(self, provider: str, success: bool) -> None:
        if provider not in self.stats:
            self.stats[provider] = {"calls": 0, "failures": 0}
        self.stats[provider]["calls"] += 1
        if not success:
            self.stats[provider]["failures"] += 1

    def reset(self) -> None:
        self.stats.clear()
        self._demoted.clear()
