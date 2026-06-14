from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SummaryMode = Literal["none", "brief", "detailed"]


class SummaryError(RuntimeError):
    pass


class SummaryEntity(BaseModel):
    name: str = Field(description="Entity name preserved from the source.")
    type: str = Field(description="Entity type such as person, project, or model.")
    why_relevant: str = Field(
        description="Short explanation of why the entity matters in the source."
    )


class SummaryOutput(BaseModel):
    summary: str = Field(description="Concise source-grounded summary text.")
    key_points: list[str] = Field(
        default_factory=list, description="Bullet-friendly takeaways."
    )
    important_entities: list[SummaryEntity] = Field(
        default_factory=list, description="Named entities that matter in the source."
    )
    verbatim_terms: list[str] = Field(
        default_factory=list, description="Important exact terms, identifiers, or URLs."
    )
    limitations: list[str] = Field(
        default_factory=list, description="Any gaps, caveats, or missing context."
    )


def summary_stub(mode: SummaryMode) -> dict[str, Any]:
    return {
        "mode": mode,
        "summary": "",
        "key_points": [],
        "important_entities": [],
        "verbatim_terms": [],
        "limitations": ["No source text or URL context was available to summarize."],
    }
