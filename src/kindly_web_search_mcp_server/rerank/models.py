"""Shared rerank engine contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """Document prepared for a rerank engine."""

    index: int
    document: str


@dataclass(frozen=True, slots=True)
class RerankResult:
    """Ranked document index and provider score."""

    index: int
    score: float


class RerankEngine(Protocol):
    """Async rerank provider boundary."""

    engine_id: str

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        model: str | None = None,
    ) -> list[RerankResult]:
        """Return ranked candidate indexes with relevance scores."""
