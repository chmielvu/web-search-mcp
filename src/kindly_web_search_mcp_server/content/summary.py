from __future__ import annotations

import json
import os
from typing import Any, Literal

import httpx


SummaryMode = Literal["none", "brief", "detailed"]


class SummaryError(RuntimeError):
    pass


def _summary_schema() -> dict[str, Any]:
    return {
        "summary": "string",
        "key_points": ["string"],
        "important_entities": [
            {"name": "string", "type": "string", "why_relevant": "string"}
        ],
        "verbatim_terms": ["string"],
        "limitations": ["string"],
    }


def _build_messages(
    markdown: str,
    *,
    mode: SummaryMode,
    focus_query: str | None,
) -> list[dict[str, str]]:
    focus = focus_query.strip() if focus_query else "None"
    source_text = markdown[:60_000]
    return [
        {
            "role": "system",
            "content": (
                "You are a source-grounded compression engine for an MCP fetch tool. "
                "Use only the provided SOURCE_TEXT. Preserve named entities, numbers, dates, "
                "version numbers, error messages, code identifiers, URLs, and stated uncertainty. "
                "Do not infer missing facts. Return valid JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"SUMMARY_MODE: {mode}\n"
                f"FOCUS_QUERY: {focus}\n"
                f"JSON_SCHEMA_SHAPE: {json.dumps(_summary_schema(), ensure_ascii=True)}\n\n"
                "SOURCE_TEXT:\n"
                f"{source_text}"
            ),
        },
    ]


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise SummaryError("Chutes response did not include choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise SummaryError("Chutes response did not include a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SummaryError("Chutes response content was empty")
    return content.strip()


def _parse_summary_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SummaryError(f"Chutes summary was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SummaryError("Chutes summary JSON was not an object")
    return parsed


async def create_summary(
    markdown: str,
    *,
    mode: SummaryMode,
    focus_query: str | None = None,
) -> dict | None:
    if mode == "none":
        return None
    if not markdown.strip():
        return {
            "mode": mode,
            "summary": "",
            "key_points": [],
            "important_entities": [],
            "verbatim_terms": [],
            "limitations": ["No source text was available to summarize."],
        }

    api_token = (os.environ.get("CHUTES_API_TOKEN") or "").strip()
    if not api_token:
        raise SummaryError("CHUTES_API_TOKEN is required for summary generation")

    model = (os.environ.get("KINDLY_SUMMARY_MODEL") or "zai-org/GLM-5-Turbo").strip()
    max_tokens = int((os.environ.get("KINDLY_SUMMARY_MAX_TOKENS") or "1200").strip())
    body = {
        "model": model,
        "messages": _build_messages(markdown, mode=mode, focus_query=focus_query),
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            "https://llm.chutes.ai/v1/chat/completions",
            headers=headers,
            json=body,
        )
        response.raise_for_status()
        summary = _parse_summary_json(_extract_message_content(response.json()))

    summary["mode"] = mode
    summary["model"] = model
    return summary
