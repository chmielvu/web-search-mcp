"""Pure-Python models for grounded entity and relation mentions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntitySpan(BaseModel):
    """A source-grounded entity mention."""

    text: str = Field(description="Surface form exactly as it appears in source text.")
    label: str = Field(description="Entity label from the extraction schema.")
    start: int | None = Field(
        default=None,
        description="Character start offset (inclusive) in the source text.",
    )
    end: int | None = Field(
        default=None,
        description="Character end offset (exclusive) in the source text.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Model-assigned confidence for this span.",
    )

    model_config = {"extra": "forbid"}


class EntityRelation(BaseModel):
    """A validated relation between two source-grounded entity mentions.

    ``confidence`` is derived from the minimum endpoint confidence because
    GLiNER2 does not expose an independent relation score.
    """

    relation: str
    head: EntitySpan
    tail: EntitySpan
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}


RelationMention = EntityRelation
