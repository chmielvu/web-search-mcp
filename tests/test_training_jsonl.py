from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from kindly_web_search_mcp_server.utils.entity import EntitySpan
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult
from kindly_web_search_mcp_server.training.query_understanding_jsonl import (
    append_query_outcome_record,
    append_query_rewrite_record,
    append_query_understanding_record,
    rewritten_slots_payload,
)


def test_training_jsonl_writes_understanding_and_outcome() -> None:
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
                raw_query="FastAPI docs",
                normalized_query="FastAPI docs",
                research_goal="find docs",
                understanding=understanding,
                model_name="vercel/amazon-nova-micro",
                prompt_name="query_understanding",
                path=str(path),
                session_id="session-1",
            )
        )
        asyncio.run(
            append_query_outcome_record(
                raw_query="FastAPI docs",
                normalized_query="FastAPI docs",
                research_goal="find docs",
                understanding=understanding,
                results=[{"title": "FastAPI", "link": "https://example.com", "snippet": "docs"}],
                path=str(path),
                session_id="session-1",
            )
        )

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["session_id"] == "session-1"
        assert first["intent"] == "general"
        assert "preserved_terms" in first
        assert first["preserved_terms"] == ["FastAPI"]
        assert second["result_count"] == 1


def test_training_jsonl_writes_rewrite_slots() -> None:
    understanding = QueryUnderstandingResult(
        intent="comparison",
        confidence=0.8,
        preserved_terms=["Elasticsearch", "OpenSearch"],
        rationale="compare engines",
    )
    slots = rewritten_slots_payload(
        (
            "opensearch elasticsearch kafka clickhouse",
            "opensearch licensing apache",
            "elasticsearch opensearch vector knn",
            "OpenSearch vs Elasticsearch multi-tenant logs Kafka ClickHouse",
            "comparison of OpenSearch and Elasticsearch for logs platforms using Kafka",
        )
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "query-understanding.jsonl"
        asyncio.run(
            append_query_rewrite_record(
                raw_query="ES vs OS",
                normalized_query="ES vs OS",
                research_goal="decide migration",
                intent="comparison",
                rewritten_branch_queries=slots,
                path=str(path),
                rewrite_model="openai/gpt-oss-120b",
                rewrite_prompt_version="10",
                run_key="run-1",
            )
        )
        asyncio.run(
            append_query_outcome_record(
                raw_query="ES vs OS",
                normalized_query="ES vs OS",
                research_goal="decide migration",
                understanding=understanding,
                results=[{"title": "cmp", "link": "https://example.com", "snippet": "vs"}],
                path=str(path),
                run_key="run-1",
                rewritten_branch_queries=slots,
                rewrite_model="openai/gpt-oss-120b",
                rewrite_prompt_version="10",
            )
        )
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        rewrite = json.loads(lines[0])
        outcome = json.loads(lines[1])
        assert rewrite["kind"] == "rewrite"
        assert rewrite["rewritten_branch_queries"]["free"] == (
            "opensearch elasticsearch kafka clickhouse"
        )
        assert rewrite["rewrite_prompt_version"] == "10"
        assert outcome["rewritten_branch_queries"]["serp1"] == "opensearch licensing apache"
        assert outcome["rewrite_model"] == "openai/gpt-oss-120b"


def test_training_jsonl_concurrent_appends_stay_valid() -> None:
    async def _run() -> list[str]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "query-understanding.jsonl"
            await asyncio.gather(
                *[
                    append_query_rewrite_record(
                        raw_query=f"q{i}",
                        normalized_query=f"q{i}",
                        research_goal="g",
                        intent="general",
                        rewritten_branch_queries={"free": f"slot-{i}"},
                        path=str(path),
                        run_key=f"run-{i}",
                    )
                    for i in range(8)
                ]
            )
            return path.read_text(encoding="utf-8").strip().splitlines()

    lines = asyncio.run(_run())
    assert len(lines) == 8
    parsed = [json.loads(line) for line in lines]
    assert {row["run_key"] for row in parsed} == {f"run-{i}" for i in range(8)}
