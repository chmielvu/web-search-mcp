"""Pydantic models for entity spans.

Pure Python; independent of GLiNER2.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntitySpan(BaseModel):
    """A grounded entity mention extracted from text.

    start/end are character offsets into the *original* text after any chunk
    offset correction has been applied by the caller (see chunk + gliner_client).
    """

    text: str = Field(
        description="Surface form of the entity exactly as matched in source text."
    )
    label: str = Field(
        description="Entity label from the extraction schema (e.g. 'package', 'version', 'repo_ref')."
    )
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
        description="Model-assigned confidence for this span (0.0-1.0).",
    )

    model_config = {"extra": "forbid"}
