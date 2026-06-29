"""Prompt helpers for the GPT-OSS-backed listwise reranker."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml

from ..models import WebSearchResult
from .builders import REASONING_EFFORT_LOW, system_header

_TEMPLATE_PATH: Final = Path(__file__).with_suffix(".yaml")


@dataclass(frozen=True, slots=True)
class RerankPromptTemplate:
    method: str
    system_message: str
    prefix: str
    body: str
    suffix: str
    output_validation_regex: str
    output_extraction_regex: str


@lru_cache(maxsize=1)
def load_rerank_prompt_template() -> RerankPromptTemplate:
    with _TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return RerankPromptTemplate(
        method=str(data["method"]),
        system_message=str(data["system_message"]).strip(),
        prefix=str(data["prefix"]).strip(),
        body=str(data["body"]).rstrip(),
        suffix=str(data["suffix"]).strip(),
        output_validation_regex=str(data["output_validation_regex"]),
        output_extraction_regex=str(data["output_extraction_regex"]),
    )


def _format_candidate(candidate: WebSearchResult) -> str:
    parts = [
        f"Title: {candidate.title}",
        f"URL: {candidate.link}",
        f"Snippet: {candidate.snippet}",
    ]
    return "\n".join(parts)


def _render_template(template: str, **values: str) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def build_llm_rerank_messages(
    *,
    query: str,
    candidates: list[tuple[int, WebSearchResult]],
    research_goal: str | None = None,
    query_type_hint: str | None = None,
) -> list[dict[str, str]]:
    template = load_rerank_prompt_template()
    context_parts = []
    if query_type_hint and query_type_hint != "general":
        context_parts.append(f"Query type: {query_type_hint}.")
    if research_goal:
        context_parts.append(f"Research goal: {research_goal}.")
    context = " ".join(context_parts) + " " if context_parts else ""
    candidate_blocks = [
        _render_template(
            template.body,
            rank=str(rank),
            candidate=_format_candidate(candidate),
        )
        for rank, candidate in candidates
    ]
    user_content = "\n\n".join(
        [
            _render_template(template.prefix, query=query, context=context),
            *candidate_blocks,
            _render_template(template.suffix, query=query),
        ]
    )
    system_content = (
        f"{system_header(REASONING_EFFORT_LOW)}\n\n{template.system_message}"
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
