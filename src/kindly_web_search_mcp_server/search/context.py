"""Search context passed between understanding, planning, and execution."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..entity.models import EntitySpan
from .intents import SearchIntent
from .options import SearchOptions


@dataclass(frozen=True, slots=True)
class SearchContext:
    raw_query: str
    normalized_query: str
    research_goal: str | None
    session_id: str | None
    intent: SearchIntent
    confidence: float
    should_decompose: bool
    rationale: str
    entities: tuple[EntitySpan, ...]
    must_keep_terms: tuple[str, ...]
    num_results: int
    search_options: SearchOptions | None

    @property
    def original_query(self) -> str:
        return self.raw_query

    def with_intent(self, intent: SearchIntent) -> "SearchContext":
        return replace(self, intent=intent)

    def with_entities(self, entities: list[EntitySpan]) -> "SearchContext":
        return replace(self, entities=tuple(entities))
