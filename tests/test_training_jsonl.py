from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.search.context import SearchContext
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult
from kindly_web_search_mcp_server.training.query_understanding_jsonl import (
    append_query_outcome_record,
    append_query_understanding_record,
)


def test_training_jsonl_writes_understanding_and_outcome() -> None:
    context = SearchContext(
        raw_query="FastAPI docs",
        normalized_query="FastAPI docs",
        research_goal="find docs",
        session_id="session-1",
        intent="general",
        confidence=0.9,
        should_decompose=False,
        rationale="clear request",
        entities=(),
        must_keep_terms=("FastAPI",),
        providers=("searxng",),
        num_results=5,
        search_options=SearchOptions(),
        profile_name="general",
    )
    understanding = QueryUnderstandingResult(
        intent="general",
        confidence=0.9,
        entities=[EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)],
        preserved_terms=["FastAPI"],
        rationale="clear request",
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "query-understanding.jsonl"

        asyncio.run(
            append_query_understanding_record(
                context=context,
                understanding=understanding,
                model_name="vercel/amazon-nova-micro",
                prompt_name="query_understanding",
                path=str(path),
                session_id=context.session_id,
            )
        )
        asyncio.run(
            append_query_outcome_record(
                context=context,
                understanding=understanding,
                results=[{"title": "FastAPI", "link": "https://example.com", "snippet": "docs"}],
                path=str(path),
                session_id=context.session_id,
            )
        )

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["session_id"] == "session-1"
        assert first["intent"] == "general"
        assert second["result_count"] == 1
