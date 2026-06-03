"""LLM-as-judge metrics with strict JSON-only output parsing.

Used exclusively by offline eval runner (never from user-facing tool paths).
Metrics implemented: tool_choice_correct, argument_correctness, source_usefulness, ranking_quality.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..analytics.evals import ensure_eval_tables
from ..settings import settings

LOGGER = logging.getLogger(__name__)

# Prompt templates force JSON-only responses. No markdown, no prose outside object.
_TOOL_CHOICE_PROMPT = """You are an expert MCP tool-use judge.
Given the user query, the list of tools the agent actually called, and the expected/required tool set for this case,
return ONLY a single JSON object (no ```, no extra text) with keys:
{{"score": <float 0.0-1.0>, "reason": <short string>, "chosen_tool": <str or null>, "expected_tool": <str or null>}}
Score 1.0 only if the primary required tool was called (or a functionally equivalent one); 0.0 if a forbidden tool was used or required was missed.
Query: {query}
Actual tool calls: {actual_calls}
Expected tool calls: {expected_calls}
"""

_ARGUMENT_CORRECTNESS_PROMPT = """You are an expert MCP tool-use judge.
Assess whether the arguments passed to the called tool(s) are correct and complete for the query intent.
Return ONLY JSON: {{"score": <0.0-1.0>, "reason": <short>, "issues": [<str>]}}
Query: {query}
Tool calls (with args): {tool_calls_with_args}
"""

_SOURCE_USEFULNESS_PROMPT = """You are a search quality judge.
Rate how useful the returned sources (titles, links, snippets) are for answering or progressing on the query.
Return ONLY JSON: {{"score": <0.0-1.0>, "reason": <short>, "useful_count": <int>}}
Query: {query}
Sources: {sources}
"""

_RANKING_QUALITY_PROMPT = """You are a result ranking judge.
Given the final ranked list of results and gold-relevant URLs or domains, rate the ranking quality (focus on early precision).
Return ONLY JSON: {{"score": <0.0-1.0>, "reason": <short>, "mrr_estimate": <float or null>}}
Query: {query}
Ranked results (top): {ranked}
Gold: {gold}
"""


def _parse_strict_json(text: str) -> dict[str, Any]:
    """Parse JSON-only output. Raises on failure. Strips optional code fences but prefers pure JSON."""
    s = (text or "").strip()
    if not s:
        raise ValueError("empty judge response")
    # allow but discourage code fence
    if s.startswith("```"):
        # take first fenced block
        parts = s.split("```")
        if len(parts) >= 2:
            inner = parts[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            s = inner
    # last resort, find first { ... } balanced naive
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    return json.loads(s)


def _call_judge_llm(prompt: str, *, model: str | None = None) -> str:
    """Best effort LLM call for judge. Uses litellm if available for multi-provider.
    Never invoked from hot user paths.
    """
    try:
        import litellm  # type: ignore
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("litellm not available for judge; returning neutral: %s", exc)
        return json.dumps({"score": 0.5, "reason": "judge llm unavailable (litellm missing)"})

    model = model or getattr(settings, "KINDLY_JUDGE_MODEL", None) or "gpt-4o-mini"
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
            # force json if provider supports
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        return content
    except Exception as exc:
        LOGGER.debug("judge llm call failed: %s", exc)
        return json.dumps({"score": 0.0, "reason": f"judge call error: {exc}"})


def judge_tool_choice_correct(
    query: str,
    actual_tool_calls: list[dict[str, Any]],
    expected_tool_calls: list[dict[str, Any]],
    *,
    eval_run_id: str = "",
    eval_case_id: str = "",
    run_key: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    """JSON-only judge for tool choice correctness."""
    prompt = _TOOL_CHOICE_PROMPT.format(
        query=query,
        actual_calls=json.dumps(actual_tool_calls, ensure_ascii=True)[:2000],
        expected_calls=json.dumps(expected_tool_calls, ensure_ascii=True)[:2000],
    )
    raw = _call_judge_llm(prompt)
    data = _parse_strict_json(raw)
    score = float(data.get("score", 0.0))
    if persist:
        _persist_judge_call(
            "tool_choice_correct",
            score,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            run_key=run_key,
            model="litellm-judge",
            payload={"raw": data},
        )
    return {"score": score, "reason": data.get("reason", ""), "raw": data}


def judge_argument_correctness(
    query: str,
    tool_calls_with_args: list[dict[str, Any]],
    *,
    eval_run_id: str = "",
    eval_case_id: str = "",
    run_key: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    prompt = _ARGUMENT_CORRECTNESS_PROMPT.format(
        query=query,
        tool_calls_with_args=json.dumps(tool_calls_with_args, ensure_ascii=True)[:2500],
    )
    raw = _call_judge_llm(prompt)
    data = _parse_strict_json(raw)
    score = float(data.get("score", 0.0))
    if persist:
        _persist_judge_call(
            "argument_correctness",
            score,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            run_key=run_key,
            model="litellm-judge",
            payload={"raw": data},
        )
    return {"score": score, "reason": data.get("reason", ""), "raw": data}


def judge_source_usefulness(
    query: str,
    sources: list[dict[str, Any]],
    *,
    eval_run_id: str = "",
    eval_case_id: str = "",
    run_key: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    prompt = _SOURCE_USEFULNESS_PROMPT.format(
        query=query, sources=json.dumps(sources, ensure_ascii=True)[:3000]
    )
    raw = _call_judge_llm(prompt)
    data = _parse_strict_json(raw)
    score = float(data.get("score", 0.0))
    if persist:
        _persist_judge_call(
            "source_usefulness",
            score,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            run_key=run_key,
            model="litellm-judge",
            payload={"raw": data},
        )
    return {"score": score, "reason": data.get("reason", ""), "raw": data}


def judge_ranking_quality(
    query: str,
    ranked: list[dict[str, Any] | str],
    gold: list[str],
    *,
    eval_run_id: str = "",
    eval_case_id: str = "",
    run_key: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    prompt = _RANKING_QUALITY_PROMPT.format(
        query=query,
        ranked=json.dumps(ranked, ensure_ascii=True)[:2000],
        gold=json.dumps(gold, ensure_ascii=True)[:1000],
    )
    raw = _call_judge_llm(prompt)
    data = _parse_strict_json(raw)
    score = float(data.get("score", 0.0))
    if persist:
        _persist_judge_call(
            "ranking_quality",
            score,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            run_key=run_key,
            model="litellm-judge",
            payload={"raw": data},
        )
    return {"score": score, "reason": data.get("reason", ""), "raw": data}


def _persist_judge_call(
    metric_name: str,
    score_value: float,
    *,
    eval_run_id: str,
    eval_case_id: str,
    run_key: str,
    model: str,
    payload: dict[str, Any],
) -> None:
    """Persist to DuckDB eval_judge_calls (and also eval_scores for unified metric)."""
    try:
        ensure_eval_tables()
        import duckdb
        from pathlib import Path

        path = Path(settings.analytics_duckdb_path)
        con = duckdb.connect(str(path))
        try:
            # ensure tables (idempotent)
            con.execute(
                "CREATE TABLE IF NOT EXISTS eval_judge_calls (judge_call_id VARCHAR, eval_run_id VARCHAR, eval_case_id VARCHAR, recorded_at TIMESTAMP, run_key VARCHAR, judge_model VARCHAR, score_value DOUBLE, payload_json VARCHAR)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS eval_scores (score_id VARCHAR, eval_run_id VARCHAR, eval_case_id VARCHAR, recorded_at TIMESTAMP, run_key VARCHAR, metric_name VARCHAR, score_value DOUBLE, payload_json VARCHAR)"
            )
            import uuid
            from datetime import datetime, UTC

            now = datetime.now(UTC)
            jid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            payload_json = json.dumps(payload, ensure_ascii=True)
            con.execute(
                "INSERT INTO eval_judge_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [jid, eval_run_id, eval_case_id, now, run_key, model, score_value, payload_json],
            )
            con.execute(
                "INSERT INTO eval_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [sid, eval_run_id, eval_case_id, now, run_key, metric_name, score_value, payload_json],
            )
            # also send metadata to Langfuse (outside user requests)
            _send_to_langfuse(
                metric_name=metric_name,
                score=score_value,
                eval_run_id=eval_run_id,
                eval_case_id=eval_case_id,
                run_key=run_key,
                model=model,
            )
        finally:
            con.close()
    except Exception as exc:  # pragma: no cover - best effort for evals
        LOGGER.debug("failed to persist judge %s: %s", metric_name, exc)


def _send_to_langfuse(
    *,
    metric_name: str,
    score: float,
    eval_run_id: str,
    eval_case_id: str,
    run_key: str,
    model: str,
) -> None:
    """Send judge metadata as score/trace to Langfuse if configured. Safe no-op if not."""
    try:
        from langfuse import get_client as get_langfuse_client  # type: ignore

        lf = get_langfuse_client()
        # create or attach trace for the eval case
        trace = lf.trace(
            name=f"eval.judge.{metric_name}",
            metadata={
                "eval_run_id": eval_run_id,
                "eval_case_id": eval_case_id,
                "run_key": run_key,
                "judge_model": model,
            },
            tags=["eval", "judge", "offline"],
        )
        trace.score(name=metric_name, value=score, comment=f"judge:{model}")
    except Exception as exc:
        LOGGER.debug("langfuse judge metadata skipped: %s", exc)


__all__ = [
    "judge_tool_choice_correct",
    "judge_argument_correctness",
    "judge_source_usefulness",
    "judge_ranking_quality",
    "_parse_strict_json",
]
