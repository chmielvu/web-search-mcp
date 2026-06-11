"""Rewrite policy model used by the 0.2 search pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
RewriteMode = Literal["bypass", "expand"]


class RewritePolicy(BaseModel):
    """Policy for query rewriting."""

    mode: RewriteMode
    reason: str
    must_keep_terms: list[str] = Field(default_factory=list)
