"""Search result set relevance judge — 4-dimensional evaluation.

Uses GPT-OSS 120B via worker router (Cerebras -> Groq -> Vercel fallback).
Evaluates search results across four dimensions: relevance, accuracy,
completeness, and source_quality. Each dimension gets a discrete grade
(excellent|good|fair|poor) and a float score (0.0-1.0), plus an
overall_score and rationale.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field

from ..telemetry.phoenix_tracing import LLMTraceContext
from ..inference.router import build_worker_router
from ..settings import settings
from .judge_prompt import JUDGE_SYSTEM_PROMPT, build_judge_user_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for 4-D structured output
# ---------------------------------------------------------------------------


class DimensionScore(BaseModel):
    """One of the four evaluation dimensions."""

    grade: Annotated[
        str,
        Field(description="Discrete grade: excellent|good|fair|poor"),
    ]
    score: Annotated[
        float,
        Field(ge=0.0, le=1.0, description="Numeric score 0.0-1.0"),
    ]
    rationale: Annotated[
        str,
        Field(description="Brief 1-2 sentence explanation of the score"),
    ]


class Judge4DResponse(BaseModel):
    """Full 4-D judge response schema."""

    relevance: DimensionScore
    accuracy: DimensionScore
    completeness: DimensionScore
    source_quality: DimensionScore
    overall_score: Annotated[
        float,
        Field(ge=0.0, le=1.0, description="Overall holistic quality score 0.0-1.0"),
    ]
    overall_rationale: Annotated[
        str,
        Field(description="1-3 sentence explanation of the overall score"),
    ]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SearchRelevanceResult:
    """Structured result for 4-D search result set evaluation."""

    # Per-dimension grades (excellent|good|fair|poor)
    relevance_grade: str
    accuracy_grade: str
    completeness_grade: str
    source_quality_grade: str

    # Per-dimension scores (0.0-1.0)
    relevance_score: float
    accuracy_score: float
    completeness_score: float
    source_quality_score: float

    # Overall
    overall_score: float
    rationale: str

    # Metadata
    judge_model: str
    duration_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    provider_name: str | None = None
    status: str = "success"
    error_type: str | None = None

    @property
    def model_used(self) -> str:
        return self.judge_model


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_results_text(results: list[Any]) -> str:
    """Format result list as passage for relevance evaluation."""
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = getattr(r, "title", "") or ""
        link = getattr(r, "link", "") or ""
        snippet = getattr(r, "snippet", "") or ""
        lines.append(f"[{i}] {title}\nURL: {link}\nSnippet: {snippet}")
    return "\n\n".join(lines) if lines else "(no results)"


# ---------------------------------------------------------------------------
# Judge class
# ---------------------------------------------------------------------------


class SearchRelevanceJudge:
    """Evaluates relevance of a search result SET to a query.

    Uses GPT-OSS 120B via worker router (Cerebras -> Groq -> Vercel fallback).
    Formats result list as a single passage for the judge.
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.judge_model
        self._router = build_worker_router()

    @staticmethod
    def _parse_response(content: object) -> Judge4DResponse:
        if isinstance(content, Judge4DResponse):
            return content
        if not isinstance(content, str):
            raise ValueError("judge generation content must be a string")
        raw = content.strip()
        marker = raw.find("[RESULT]")
        candidates = [raw]
        if marker >= 0:
            candidates.insert(0, raw[marker + len("[RESULT]") :].strip())
        for candidate in candidates:
            try:
                return Judge4DResponse.model_validate(json.loads(candidate))
            except (ValueError, TypeError):
                match = re.search(r"\{.*\}", candidate, re.DOTALL)
                if match:
                    try:
                        return Judge4DResponse.model_validate(json.loads(match.group(0)))
                    except (ValueError, TypeError):
                        continue
        raise ValueError("legacy judge response did not contain valid 4-D JSON")

    async def evaluate(
        self,
        query: str,
        intent: str,
        results: list[Any],
        research_goal: str | None = None,
        rewrite_variants: list[Any] | None = None,
        langfuse: LLMTraceContext | None = None,
    ) -> SearchRelevanceResult:
        """Evaluate search results across 4 dimensions."""
        if not results:
            return SearchRelevanceResult(
                relevance_grade="poor",
                accuracy_grade="poor",
                completeness_grade="poor",
                source_quality_grade="poor",
                relevance_score=0.0,
                accuracy_score=0.0,
                completeness_score=0.0,
                source_quality_score=0.0,
                overall_score=0.0,
                rationale="No results to evaluate.",
                judge_model=self.model,
                provider_name=None,
                duration_ms=0.0,
                input_tokens=None,
                output_tokens=None,
                error="no_results",
                status="error",
                error_type="no_results",
            )

        start = asyncio.get_event_loop().time()
        results_text = _format_results_text(results)
        user_prompt = build_judge_user_prompt(
            query=query,
            research_goal=(research_goal or "").strip(),
            intent=intent,
            results_text=results_text,
            tool_name="web_search",
        )

        try:
            generation = await self._router.complete_json(
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                timeout_seconds=settings.judge_timeout_seconds,
                response_model=Judge4DResponse,
                langfuse=langfuse,
            )

            judge_output = self._parse_response(generation.content)

            duration_ms = round((asyncio.get_event_loop().time() - start) * 1000.0, 3)

            return SearchRelevanceResult(
                relevance_grade=judge_output.relevance.grade,
                accuracy_grade=judge_output.accuracy.grade,
                completeness_grade=judge_output.completeness.grade,
                source_quality_grade=judge_output.source_quality.grade,
                relevance_score=judge_output.relevance.score,
                accuracy_score=judge_output.accuracy.score,
                completeness_score=judge_output.completeness.score,
                source_quality_score=judge_output.source_quality.score,
                overall_score=judge_output.overall_score,
                rationale=judge_output.overall_rationale,
                judge_model=getattr(generation, "model_used", self.model),
                provider_name=getattr(getattr(generation, "endpoint", None), "name", None),
                duration_ms=duration_ms,
                input_tokens=getattr(generation, "input_tokens", None),
                output_tokens=getattr(generation, "output_tokens", None),
            )

        except Exception as exc:
            logger.debug("4-D judge failed: %s", exc)
            duration_ms = round((asyncio.get_event_loop().time() - start) * 1000.0, 3)
            return SearchRelevanceResult(
                relevance_grade="poor",
                accuracy_grade="poor",
                completeness_grade="poor",
                source_quality_grade="poor",
                relevance_score=0.0,
                accuracy_score=0.0,
                completeness_score=0.0,
                source_quality_score=0.0,
                overall_score=0.0,
                rationale="",
                judge_model=self.model,
                provider_name=None,
                duration_ms=duration_ms,
                input_tokens=None,
                output_tokens=None,
                error=f"{type(exc).__name__}: {exc}",
                status="error",
                error_type="parse_or_provider_error",
            )


__all__ = ["SearchRelevanceJudge", "SearchRelevanceResult"]
