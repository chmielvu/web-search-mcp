"""Pydantic models for minimal eval case fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    tool_name: str
    required: bool = True
    forbidden: bool = False


class CandidateSet(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    eval_case_id: str = Field(
        default="",
        validation_alias=AliasChoices("eval_case_id", "id"),
    )
    suite_name: str = "code_search"
    query: str
    research_goal: str | None = None
    category: str | None = None
    mode: str | None = None
    expected_repo: str | None = None
    expected_path_substring: str | None = None
    expected_kind: str | None = None
    expected_line_span: list[int] | None = None
    provider_scope: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    gold_urls: list[str] = Field(default_factory=list)
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    candidate_sets: list[CandidateSet] = Field(default_factory=list)


def load_eval_cases_from_jsonl(path: str | Path) -> list[EvalCase]:
    """Load EvalCase models from a JSONL file."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"JSONL eval case fixture not found: {file_path}")
    cases: list[EvalCase] = []
    for line_num, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at line {line_num} in {file_path}: {exc}") from exc
        cases.append(EvalCase.model_validate(data))
    return cases


def save_eval_cases_to_jsonl(cases: list[EvalCase], path: str | Path) -> None:
    """Save EvalCase models to a JSONL file."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [case.model_dump_json(by_alias=False, exclude_none=False) for case in cases]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Aliases for convenience
load_eval_cases = load_eval_cases_from_jsonl
save_eval_cases = save_eval_cases_to_jsonl
