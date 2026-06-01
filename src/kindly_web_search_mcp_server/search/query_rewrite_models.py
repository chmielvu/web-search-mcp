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
    "practitioner_opinion",
    "bug_report",
    "how_to",
    "subquestion",
]
QueryTarget = Literal["keyword", "neural", "community", "all"]

KEYWORD_PROVIDER_NAMES = frozenset({"searxng", "ddg", "brave", "tavily"})
COMMUNITY_PROVIDER_NAMES = frozenset(
    {"hackernews", "reddit", "github_graphql", "stackexchange"}
)
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


class ProviderRouting(BaseModel):
    keyword: bool = True
    neural: bool = True
    community: bool = False


class ClassifierOutput(BaseModel):
    intent: RewriteIntent
    should_decompose: bool
    confidence: float = Field(ge=0.0, le=1.0)
    routing: ProviderRouting = Field(default_factory=ProviderRouting)


class SubQuestion(BaseModel):
    question: str = Field(description="Standalone sub-question or search query.")
    target: QueryTarget = Field(description="Provider category to search.")
    why: str = Field(description="Short reason for this branch.")
    weight: float = Field(default=1.0, ge=0.8, le=1.2)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = normalize_query(value)
        if not value:
            raise ValueError("question cannot be empty")
        return value

    @field_validator("why")
    @classmethod
    def validate_subquestion_why(cls, value: str) -> str:
        value = normalize_query(value)
        if not value:
            raise ValueError("why cannot be empty")
        return value


class QueryDecompositionOutput(BaseModel):
    should_decompose: bool = False
    sub_questions: list[SubQuestion] = Field(default_factory=list)


class QueryRewriteOutput(BaseModel):
    variants: list[QueryVariant] = Field(default_factory=list)


class QueryRewritePlan(BaseModel):
    original_query: str
    policy: RewritePolicy
    variants: list[QueryVariant]
    final_queries: list[str]
    classifier: ClassifierOutput | None = None
    decomposition: QueryDecompositionOutput | None = None
