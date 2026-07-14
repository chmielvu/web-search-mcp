"""Write-only JSONL sink for query-understanding training data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..search.understanding.models import QueryUnderstanding


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
        "model": model_name,
        "prompt": prompt_name,
        "session_id": session_id,
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
        "session_id": session_id,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    await _append_line(target, line)


async def _append_line(path: Path, line: str) -> None:
    import aiofiles

    async with aiofiles.open(path, "a", encoding="utf-8") as handle:
        await handle.write(line + "\n")