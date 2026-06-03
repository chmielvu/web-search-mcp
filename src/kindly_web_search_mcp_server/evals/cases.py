"""Pydantic models for minimal eval case fixtures."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExpectedToolCall(BaseModel):
    tool_name: str
    required: bool = True
    forbidden: bool = False


class CandidateSet(BaseModel):
    name: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class EvalCase(BaseModel):
    eval_case_id: str
    suite_name: str
    query: str
    research_goal: str | None = None
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    candidate_sets: list[CandidateSet] = Field(default_factory=list)
