"""Write-only JSONL sink for query-understanding training data."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import aiofiles

from ...search.understanding.models import QueryUnderstanding


REWRITE_SLOT_ORDER = ("free", "serp1", "serp2", "semantic_tavily", "semantic_exa")

_append_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _append_lock
    if _append_lock is None:
        _append_lock = asyncio.Lock()
    return _append_lock


def rewritten_slots_payload(queries: Sequence[str] | None) -> dict[str, str] | None:
    """Map planner rewrite slots onto named JSONL fields."""
    if not queries:
        return None
    payload = {
        name: str(value)
        for name, value in zip(REWRITE_SLOT_ORDER, queries, strict=False)
        if str(value)
    }
    return payload or None


async def append_query_understanding_record(
    *,
    raw_query: str,
    normalized_query: str,
    research_goal: str | None,
    understanding: QueryUnderstanding,
    model_name: str,
    prompt_name: str,
    path: str,
    session_id: str | None = None,
    decision_path: str | None = None,
    classifier_scores: dict[str, float] | None = None,
    classifier_model: str | None = None,
    classifier_endpoint: str | None = None,
    classifier_latency_ms: float | None = None,
    confidence_threshold: float | None = None,
    fallback_reason: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": raw_query,
        "normalized_query": normalized_query,
        "research_goal": research_goal,
        "intent": understanding.intent,
        "confidence": understanding.confidence,
        "should_decompose": understanding.should_decompose,
        "rationale": understanding.rationale,
        "preserved_terms": understanding.preserved_terms,
        "entities": [entity.model_dump() for entity in understanding.entities],
        "relations": [relation.model_dump() for relation in understanding.relations],
        "model": model_name,
        "prompt": prompt_name,
        "session_id": session_id,
        "decision_path": decision_path,
        "classifier_scores": classifier_scores,
        "classifier_model": classifier_model,
        "classifier_endpoint": classifier_endpoint,
        "classifier_latency_ms": classifier_latency_ms,
        "confidence_threshold": confidence_threshold,
        "fallback_reason": fallback_reason,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def append_query_rewrite_record(
    *,
    raw_query: str,
    normalized_query: str,
    research_goal: str | None,
    intent: str | None,
    rewritten_branch_queries: dict[str, str] | None,
    path: str,
    rewrite_model: str | None = None,
    rewrite_error: str | None = None,
    rewrite_prompt_version: str | None = None,
    session_id: str | None = None,
    run_key: str | None = None,
) -> None:
    """Persist planner rewrite slots even when later search persistence fails."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "rewrite",
        "query": raw_query,
        "normalized_query": normalized_query,
        "research_goal": research_goal,
        "intent": intent,
        "rewritten_branch_queries": rewritten_branch_queries,
        "rewrite_model": rewrite_model,
        "rewrite_error": rewrite_error,
        "rewrite_prompt_version": rewrite_prompt_version,
        "session_id": session_id,
        "run_key": run_key,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def append_query_outcome_record(
    *,
    raw_query: str,
    normalized_query: str,
    research_goal: str | None,
    understanding: QueryUnderstanding,
    results: list[dict[str, object]],
    path: str,
    session_id: str | None = None,
    run_key: str | None = None,
    rewritten_branch_queries: dict[str, str] | None = None,
    rewrite_model: str | None = None,
    rewrite_error: str | None = None,
    rewrite_prompt_version: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": raw_query,
        "normalized_query": normalized_query,
        "research_goal": research_goal,
        "intent": understanding.intent,
        "confidence": understanding.confidence,
        "results": results,
        "result_count": len(results),
        "session_id": session_id,
        "run_key": run_key,
        "rewritten_branch_queries": rewritten_branch_queries,
        "rewrite_model": rewrite_model,
        "rewrite_error": rewrite_error,
        "rewrite_prompt_version": rewrite_prompt_version,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def _append_line(path: Path, line: str) -> None:
    async with _lock():
        async with aiofiles.open(path, "a", encoding="utf-8") as handle:
            await handle.write(line + "\n")
