"""GPT-OSS-backed listwise reranking."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import re

from ..llm.phoenix_tracing import LLMTraceContext
from ..llm import build_llm_worker
from ..models import WebSearchResult
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.rerank_llm import build_llm_rerank_messages, load_rerank_prompt_template
from .models import RerankResult

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMRerankOutcome:
    endpoint_name: str
    model: str
    ranked: list[RerankResult]
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: Exception | None = None

    @property
    def model_used(self) -> str:
        return self.model


def _build_candidate_window(
    candidates: list[WebSearchResult],
    candidate_limit: int,
) -> list[tuple[int, WebSearchResult]]:
    limit = len(candidates) if candidate_limit <= 0 else min(candidate_limit, len(candidates))
    return list(enumerate(candidates[:limit], start=1))


def _parse_ranked_ids(output: str, candidate_count: int) -> list[int]:
    def _coerce_ranked_ids(raw_ids: object) -> list[int]:
        if not isinstance(raw_ids, list):
            return []
        ordered_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in raw_ids:
            try:
                candidate_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if candidate_id < 1 or candidate_id > candidate_count:
                _logger.warning(
                    "LLM rerank returned out-of-range index %d (candidate_count=%d), skipping.",
                    candidate_id,
                    candidate_count,
                )
                continue
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            ordered_ids.append(candidate_id)
        for candidate_id in range(1, candidate_count + 1):
            if candidate_id not in seen:
                ordered_ids.append(candidate_id)
        return ordered_ids

    stripped = output.strip()

    # Try JSON fallback (in case some models still return JSON)
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("ranked_candidate_ids", "ranked_ids", "ranking"):
                ranked_ids = _coerce_ranked_ids(payload.get(key))
                if ranked_ids:
                    return ranked_ids

    # Extract all [N] patterns from the output — handles both wrapped and raw formats
    template = load_rerank_prompt_template()
    extracted_ids_raw = [int(match) for match in re.findall(template.output_extraction_regex, stripped)]
    if not extracted_ids_raw:
        raise ValueError(f"LLM rerank returned no ranked candidate ids. Output: {output!r}")

    ordered_ids: list[int] = []
    seen: set[int] = set()
    for candidate_id in extracted_ids_raw:
        if candidate_id < 1 or candidate_id > candidate_count:
            _logger.warning(
                "LLM rerank returned out-of-range index %d (candidate_count=%d), skipping.",
                candidate_id,
                candidate_count,
            )
            continue
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        ordered_ids.append(candidate_id)

    for candidate_id in range(1, candidate_count + 1):
        if candidate_id not in seen:
            ordered_ids.append(candidate_id)
    return ordered_ids


async def rerank_with_llm(
    query: str,
    candidates: list[WebSearchResult],
    *,
    top_k: int,
    candidate_limit: int | None = None,
    query_type_hint: str | None = None,
    research_goal: str | None = None,
    instruction: str | None = None,
    timeout_seconds: float | None = None,
    session_id: str | None = None,
) -> LLMRerankOutcome:
    window = _build_candidate_window(
        candidates,
        len(candidates) if candidate_limit is None else candidate_limit,
    )
    if not window:
        return LLMRerankOutcome(
            endpoint_name="gpt-oss-worker",
            model="gpt-oss-120b",
            ranked=[],
        )

    worker = build_llm_worker()
    response = await worker.complete_text_messages(
        task="rerank",
        messages=build_llm_rerank_messages(
            query=query,
            candidates=window,
            research_goal=research_goal,
            query_type_hint=query_type_hint,
        ),
        temperature=0.0,
        timeout_seconds=timeout_seconds,
        reasoning_effort=REASONING_EFFORT_LOW,
        langfuse=LLMTraceContext(
            trace_name="llm_rerank",
            session_id=session_id,
            metadata={
                "task": "rerank",
                "candidate_count": len(window),
                "top_k": top_k,
                "query_type_hint": query_type_hint or "",
                "research_goal": research_goal or "",
            },
        ),
    )
    ranked_ids = _parse_ranked_ids(response.content, len(window))
    ranked = [
        RerankResult(index=candidate_id - 1, score=math.exp(-0.3 * (position - 1)))
        for position, candidate_id in enumerate(ranked_ids, start=1)
    ]
    return LLMRerankOutcome(
        endpoint_name=response.endpoint_name,
        model=response.model_name,
        ranked=ranked,
        input_tokens=getattr(response, "input_tokens", None),
        output_tokens=getattr(response, "output_tokens", None),
    )
