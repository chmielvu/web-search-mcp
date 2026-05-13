from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .normalize import normalize_query
from .query_policy import RewritePolicy

RewriteIntent = Literal["code", "general_research", "comparison"]
QueryVariantKind = Literal[
    "original",
    "official_docs",
    "community_issues",
    "expanded",
    "focused",
    "entity_a",
    "entity_b",
    "neural_task",
]
QueryTarget = Literal["keyword", "neural", "all"]

KEYWORD_PROVIDER_NAMES = frozenset({"searxng", "ddg", "brave", "tavily"})
NEURAL_PROVIDER_NAMES = frozenset({"gemini", "composio_llm_search", "jina"})


class QueryVariant(BaseModel):
    kind: QueryVariantKind
    target: QueryTarget
    query: str = Field(description="Search query or grounded-provider task.")
    why: str = Field(description="Short reason for this variant.")
    weight: float = Field(default=1.0, ge=0.8, le=1.2)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = normalize_query(value)
        if not value:
            raise ValueError("query cannot be empty")
        return value

    @field_validator("why")
    @classmethod
    def validate_why(cls, value: str) -> str:
        value = normalize_query(value)
        if not value:
            raise ValueError("why cannot be empty")
        return value


class QueryRewriteOutput(BaseModel):
    variants: list[QueryVariant] = Field(default_factory=list)


class QueryRewritePlan(BaseModel):
    original_query: str
    policy: RewritePolicy
    variants: list[QueryVariant]
    final_queries: list[str]
