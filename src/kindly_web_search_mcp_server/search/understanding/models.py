"""Query understanding result models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...entity.models import EntityRelation, EntitySpan
from ..intents import SearchIntent


class QueryUnderstandingResult(BaseModel):
    schema_version: Literal["0.3"] = "0.3"
    intent: SearchIntent
    confidence: float = Field(ge=0.0, le=1.0)
    entities: list[EntitySpan] = Field(default_factory=list)
    relations: list[EntityRelation] = Field(default_factory=list)
    preserved_terms: list[str] = Field(default_factory=list)
    compared_entities: list[str] = Field(default_factory=list)
    time_sensitivity: Literal["none", "recent", "current", "historical"] = "none"
    domain_hints: list[str] = Field(default_factory=list)
    rationale: str
    should_decompose: bool = False

    model_config = {"extra": "forbid"}


QueryUnderstanding = QueryUnderstandingResult
