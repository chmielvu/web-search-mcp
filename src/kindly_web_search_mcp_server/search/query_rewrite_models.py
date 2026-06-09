"""Query rewrite variant model for the 0.2 pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .normalize import normalize_query


class QueryVariant(BaseModel):
    kind: str
    target: str
    query: str = Field(description="Search query or grounded-provider task.")
    why: str = Field(description="Short reason for this variant.")
    weight: float = Field(default=1.0, ge=0.8, le=1.2)
    branch_type: str | None = None
    must_keep_terms: list[str] = Field(default_factory=list)
    max_results: int | None = Field(default=None, ge=1, le=20)
    reason: str | None = None

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
