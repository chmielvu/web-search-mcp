"""Prompt helpers for the XML listwise LLM reranker."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape as _xml_escape
from pathlib import Path
from typing import Final

import yaml

from ..search.evidence import evidence_passages
from ..search.types import ScoredHit
from .builders import REASONING_EFFORT_LOW, system_header

_TEMPLATE_PATH: Final[Path] = Path(__file__).with_suffix(".yaml")


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
def load_rerank_system_message() -> str:
    with _TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return str(data["system_message"]).strip()


@lru_cache(maxsize=1)
def load_rerank_prompt_template() -> RerankPromptTemplate:
    with _TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return RerankPromptTemplate(
        method=str(data["method"]),
        system_message=str(data["system_message"]).strip(),
        prefix=str(data["prefix_user"]).strip(),
        body=str(data["body_user"]).rstrip(),
        suffix=str(data["suffix_user"]).strip(),
        output_validation_regex=str(data["output_validation_regex"]),
        output_extraction_regex=str(data["output_extraction_regex"]),
    )


def _escape_prompt_xml(value: str | None) -> str:
    return _xml_escape(" ".join((value or "").split()), quote=True)


def _format_candidate(candidate: ScoredHit) -> str:
    lines = [
        '<candidate_data type="untrusted_search_result">',
        f"Title: {_escape_prompt_xml(candidate.hit.title)}",
        f"URL: {_escape_prompt_xml(candidate.hit.url)}",
        f"Snippet: {_escape_prompt_xml(candidate.hit.snippet)}",
    ]
    if candidate.hit.published:
        lines.append(f"PublishedDate: {_escape_prompt_xml(candidate.hit.published)}")
    if candidate.hit.source_name:
        lines.append(f"Source: {_escape_prompt_xml(candidate.hit.source_name)}")
    if candidate.hit.source_kind:
        lines.append(f"SourceKind: {_escape_prompt_xml(candidate.hit.source_kind)}")
    if candidate.hit.answer_kind:
        lines.append(f"AnswerKind: {_escape_prompt_xml(candidate.hit.answer_kind)}")
    if candidate.providers:
        lines.append(f"Providers: {_escape_prompt_xml(', '.join(candidate.providers))}")
    for highlight in evidence_passages(candidate.hit)[1:4]:
        lines.append(f"Evidence: {_escape_prompt_xml(highlight)}")
    lines.append("</candidate_data>")
    return "\n".join(lines)


def _render_template(template: str, **values: str) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def build_llm_rerank_messages(
    *,
    query: str,
    candidates: list[tuple[int, int, ScoredHit]],
    research_goal: str | None = None,
    query_type_hint: str | None = None,
) -> list[dict[str, str]]:
    del query_type_hint
    template = load_rerank_prompt_template()
    candidate_blocks = [
        _render_template(
            template.body,
            rank=str(display_id),
            candidate=_format_candidate(candidate),
        )
        for display_id, _, candidate in candidates
    ]
    user_content = "\n\n".join(
        [
            _render_template(
                template.prefix,
                query=_escape_prompt_xml(query),
                research_goal=_escape_prompt_xml(research_goal or query),
                candidate_blocks="\n\n".join(candidate_blocks),
            ),
            _render_template(template.suffix),
        ]
    )
    return [
        {
            "role": "system",
            "content": f"{system_header(REASONING_EFFORT_LOW)}\n\n{template.system_message}",
        },
        {"role": "user", "content": user_content},
    ]
