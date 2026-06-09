"""Query understanding result models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...entity.models import EntitySpan
from ..intents import SearchIntent


class ProviderRoutingHints(BaseModel):
    keyword: bool = True
    neural: bool = True
    community: bool = False


class RewriteHints(BaseModel):
    style: str = "compact"
    variant_count: int = 2
    preserve_order: bool = True


class QueryUnderstandingResult(BaseModel):
    schema_version: Literal["0.2"] = "0.2"
    intent: SearchIntent
    confidence: float = Field(ge=0.0, le=1.0)
    entities: list[EntitySpan] = Field(default_factory=list)
    preserved_terms: list[str] = Field(default_factory=list)
    compared_entities: list[str] = Field(default_factory=list)
    time_sensitivity: Literal["none", "recent", "current", "historical"] = "none"
    domain_hints: list[str] = Field(default_factory=list)
    provider_hints: ProviderRoutingHints = Field(default_factory=ProviderRoutingHints)
    rewrite_hints: RewriteHints = Field(default_factory=RewriteHints)
    rationale: str
    should_decompose: bool = False

    @property
    def must_keep_terms(self) -> list[str]:
        return list(self.preserved_terms)

    model_config = {"extra": "forbid"}


QueryUnderstanding = QueryUnderstandingResult
