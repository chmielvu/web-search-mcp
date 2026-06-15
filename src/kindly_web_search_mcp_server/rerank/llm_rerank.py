"""GPT-OSS-backed listwise reranking."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..llm.phoenix_tracing import LLMTraceContext
from ..llm import StructuredLLMRequest, build_llm_worker
from ..models import WebSearchResult
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.rerank_llm import build_llm_rerank_messages, load_rerank_prompt_template
from .models import RerankResult


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
    template = load_rerank_prompt_template()
    cleaned = " ".join(output.split())
    if not re.fullmatch(template.output_validation_regex, cleaned):
        raise ValueError(f"Unexpected listwise rerank output: {output!r}")

    extracted_ids = [int(match) for match in re.findall(template.output_extraction_regex, cleaned)]
    if not extracted_ids:
        raise ValueError("LLM rerank returned no ranked candidate ids.")

    ordered_ids: list[int] = []
    seen: set[int] = set()
    for candidate_id in extracted_ids:
        if candidate_id < 1 or candidate_id > candidate_count:
            raise ValueError(f"LLM rerank returned out-of-range index {candidate_id}.")
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
    request = StructuredLLMRequest(
        task="rerank",
        messages=build_llm_rerank_messages(query=query, candidates=window),
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
    response = await worker.complete_text_messages(
        task=request.task,
        messages=request.messages,
        temperature=request.temperature,
        timeout_seconds=request.timeout_seconds,
        reasoning_effort=request.reasoning_effort,
        langfuse=request.langfuse,
    )
    ranked_ids = _parse_ranked_ids(response.content, len(window))
    ranked = [
        RerankResult(index=candidate_id - 1, score=1.0 / position)
        for position, candidate_id in enumerate(ranked_ids, start=1)
    ]
    return LLMRerankOutcome(
        endpoint_name=response.endpoint_name,
        model=response.model_name,
        ranked=ranked,
        input_tokens=getattr(response, "input_tokens", None),
        output_tokens=getattr(response, "output_tokens", None),
    )
