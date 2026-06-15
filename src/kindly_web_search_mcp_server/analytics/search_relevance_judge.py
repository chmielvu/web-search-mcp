"""Search result set relevance judge using GPT-OSS 120B."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..settings import settings
from ..llm.langfuse_tracing import LangfuseTraceContext
from ..llm.router import build_worker_router

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchRelevanceResult:
    """Structured result for search result set relevance."""

    relevance_raw: int  # 1-4 (Irrelevant -> Perfectly relevant)
    relevance_score: float  # 0.0-1.0 normalized
    reasoning: str
    judge_model: str
    duration_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None

    @property
    def model_used(self) -> str:
        return self.judge_model


_RELEVANCE_SYSTEM_PROMPT = """You are an information retrieval evaluation expert.
Your task is to evaluate how relevant a set of search results is to a user's query.

Consider:
- The user's original query and their research goal (if provided)
- The search intent: general research, ai_coding, digital_humanities, or comparison
- The rewritten query variants used for multi-branch search
- How well each result matches the query topic
- Whether the results collectively address the user's information need

Return a JSON object with exactly these fields:
{"score": <1-4>, "reasoning": "<brief explanation>"}

Scoring scale:
1 = Irrelevant: Results do not match the query at all
2 = Related: Results are loosely related but miss key aspects
3 = Highly relevant: Results directly address the query with good coverage
4 = Perfectly relevant: Results comprehensively and precisely answer the query"""


def _format_results_text(results: list[Any]) -> str:
    """Format result list as passage for relevance evaluation."""
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = getattr(r, "title", "") or ""
        link = getattr(r, "link", "") or ""
        snippet = getattr(r, "snippet", "") or ""
        lines.append(f"[{i}] {title}\nURL: {link}\nSnippet: {snippet}")
    return "\n\n".join(lines) if lines else "(no results)"


def _format_rewrite_variants(rewrite_variants: list[Any] | None) -> str:
    """Format rewrite variants for the judge prompt."""
    if not rewrite_variants:
        return ""
    lines: list[str] = []
    for i, v in enumerate(rewrite_variants):
        query = getattr(v, "query", "") or (v.get("query", "") if isinstance(v, dict) else "")
        kind = getattr(v, "kind", "") or (v.get("kind", "") if isinstance(v, dict) else "")
        raw_weight = getattr(v, "weight", None) if hasattr(v, "weight") else (
            v.get("weight", None) if isinstance(v, dict) else None
        )
        weight_str = f" (weight={raw_weight:.2f})" if raw_weight is not None else ""
        lines.append(f"  [{i}] {query} [{kind}]{weight_str}")
    return "\n".join(lines)


def _parse_relevance_response(response_text: str) -> tuple[int, str]:
    """Parse structured JSON response from judge LLM."""
    import json

    s = (response_text or "").strip()
    if not s:
        return 1, "empty response"

    # Strip markdown code fences if present
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            inner = parts[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            s = inner

    # Find first balanced { ... }
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]

    try:
        data = json.loads(s)
        score = int(data.get("score", 1))
        reasoning = str(data.get("reasoning", ""))
        # Clamp to valid range
        score = max(1, min(4, score))
        return score, reasoning
    except (json.JSONDecodeError, ValueError, TypeError):
        return 1, f"parse error: {response_text[:200]}"


class SearchRelevanceJudge:
    """Evaluates relevance of a search result SET to a query.

    Uses GPT-OSS 120B via worker router (Cerebras -> Groq -> Vercel fallback).
    Formats result list as a single passage for the judge.
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.judge_model
        self._router = build_worker_router()

    async def evaluate(
        self,
        query: str,
        intent: str,
        results: list[Any],
        research_goal: str | None = None,
        rewrite_variants: list[Any] | None = None,
        langfuse: LangfuseTraceContext | None = None,
    ) -> SearchRelevanceResult:
        """Evaluate relevance of search result set to query."""
        if not results:
            return SearchRelevanceResult(
                relevance_raw=1,
                relevance_score=0.0,
                reasoning="No results to evaluate",
                judge_model=self.model,
                duration_ms=0.0,
                input_tokens=None,
                output_tokens=None,
                error="no_results",
            )

        start = asyncio.get_event_loop().time()
        results_text = _format_results_text(results)

        # Build prompt sections
        prompt_parts = [
            f"Query: {query}",
            f"Search intent: {intent}",
        ]

        if research_goal:
            prompt_parts.append(f"Research goal: {research_goal}")

        if rewrite_variants:
            rewrites_text = _format_rewrite_variants(rewrite_variants)
            if rewrites_text:
                prompt_parts.append(f"Rewritten query variants:\n{rewrites_text}")

        prompt_parts.extend([
            "",
            "--- Search results ---",
            results_text,
            "--- End results ---",
            "",
            "Evaluate the relevance of these search results to the query.",
        ])

        user_prompt = "\n".join(prompt_parts)

        try:
            generation = await self._router.complete_json(
                messages=[
                    {"role": "system", "content": _RELEVANCE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                timeout_seconds=settings.judge_timeout_seconds,
                langfuse=langfuse,
            )

            relevance_raw, reasoning = _parse_relevance_response(generation.content)
            duration_ms = round(
                (asyncio.get_event_loop().time() - start) * 1000.0, 3
            )
            relevance_score = (relevance_raw - 1) / 3.0  # Normalize to 0.0-1.0

            return SearchRelevanceResult(
                relevance_raw=relevance_raw,
                relevance_score=relevance_score,
                reasoning=reasoning,
                judge_model=self.model,
                duration_ms=duration_ms,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
            )

        except Exception as exc:
            logger.debug("relevance judge failed: %s", exc)
            duration_ms = round(
                (asyncio.get_event_loop().time() - start) * 1000.0, 3
            )
            return SearchRelevanceResult(
                relevance_raw=1,
                relevance_score=0.0,
                reasoning="",
                judge_model=self.model,
                duration_ms=duration_ms,
                input_tokens=None,
                output_tokens=None,
                error=f"{type(exc).__name__}: {exc}",
            )


__all__ = ["SearchRelevanceJudge", "SearchRelevanceResult"]
