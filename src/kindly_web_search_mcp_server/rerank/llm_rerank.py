"""GPT-OSS-backed listwise reranking with position debiasing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import random
import re

from ..llm import build_llm_worker
from ..llm.phoenix_tracing import LLMTraceContext
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
) -> list[tuple[int, int, WebSearchResult]]:
    limit = len(candidates) if candidate_limit <= 0 else min(candidate_limit, len(candidates))
    pool = candidates[:limit]
    indexed = list(enumerate(pool))
    seed_material = "|".join(candidate.link for candidate in pool).encode("utf-8")
    seed = int(hashlib.md5(seed_material).hexdigest(), 16) % (2**32)
    random.Random(seed).shuffle(indexed)
    return [
        (display_id, original_idx, candidate)
        for display_id, (original_idx, candidate) in enumerate(indexed, 1)
    ]


def _parse_ranked_ids(output: str, candidate_count: int) -> list[int]:
    template = load_rerank_prompt_template()
    match = re.search(template.output_extraction_regex, output.strip(), flags=re.DOTALL)
    if not match:
        raise ValueError(f"LLM rerank returned no <final_ranking> block. Output: {output!r}")
    extracted = [int(value) for value in re.findall(r"\[(\d+)\]", match.group(1))]
    ordered: list[int] = []
    seen: set[int] = set()
    for candidate_id in extracted:
        if candidate_id < 1 or candidate_id > candidate_count:
            _logger.warning("LLM rerank returned out-of-range display ID %d", candidate_id)
            continue
        if candidate_id not in seen:
            seen.add(candidate_id)
            ordered.append(candidate_id)
    ordered.extend(
        candidate_id for candidate_id in range(1, candidate_count + 1) if candidate_id not in seen
    )
    return ordered


def _position_score(position: int, total: int) -> float:
    if total <= 1:
        return 1.0
    return 1.0 - (position - 1) / (total - 1)


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
    del instruction
    window = _build_candidate_window(
        candidates, len(candidates) if candidate_limit is None else candidate_limit
    )
    if not window:
        return LLMRerankOutcome(endpoint_name="gpt-oss-worker", model="gpt-oss-120b", ranked=[])
    try:
        response = await build_llm_worker().complete_text_messages(
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
                metadata={"task": "rerank", "candidate_count": len(window), "top_k": top_k},
            ),
        )
        ranked_ids = _parse_ranked_ids(response.content, len(window))
        display_to_original = {display_id: original_idx for display_id, original_idx, _ in window}
        ranked = [
            RerankResult(
                index=display_to_original[candidate_id],
                score=_position_score(position, len(ranked_ids)),
            )
            for position, candidate_id in enumerate(ranked_ids, 1)
        ]
        return LLMRerankOutcome(
            endpoint_name=response.endpoint_name,
            model=response.model_name,
            ranked=ranked,
            input_tokens=getattr(response, "input_tokens", None),
            output_tokens=getattr(response, "output_tokens", None),
        )
    except Exception as exc:
        _logger.warning("LLM rerank failed: %s", exc)
        return LLMRerankOutcome(
            endpoint_name="gpt-oss-worker",
            model="gpt-oss-120b",
            ranked=[],
            error=exc,
        )
