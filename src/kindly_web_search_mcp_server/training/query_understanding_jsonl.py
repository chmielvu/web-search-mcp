"""Write-only JSONL sink for query-understanding training data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..search.context import SearchContext
from ..search.understanding.models import QueryUnderstanding


async def append_query_understanding_record(
    *,
    context: SearchContext,
    understanding: QueryUnderstanding,
    model_name: str,
    prompt_name: str,
    path: str,
    session_id: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": context.raw_query,
        "normalized_query": context.normalized_query,
        "research_goal": context.research_goal,
        "profile": context.profile_name,
        "intent": understanding.intent,
        "confidence": understanding.confidence,
        "should_decompose": understanding.should_decompose,
        "rationale": understanding.rationale,
        "must_keep_terms": understanding.must_keep_terms,
        "entities": [entity.model_dump() for entity in understanding.entities],
        "model": model_name,
        "prompt": prompt_name,
        "session_id": session_id,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def append_query_outcome_record(
    *,
    context: SearchContext,
    understanding: QueryUnderstanding,
    results: list[dict[str, object]],
    path: str,
    session_id: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": context.raw_query,
        "normalized_query": context.normalized_query,
        "research_goal": context.research_goal,
        "profile": context.profile_name,
        "intent": understanding.intent,
        "confidence": understanding.confidence,
        "result_count": len(results),
        "results": results,
        "session_id": session_id,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def _append_line(path: Path, line: str) -> None:
    from asyncio import to_thread

    def _write() -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.write("\n")

    await to_thread(_write)
