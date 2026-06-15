from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Sequence

from google import genai
from google.genai import types

from ..telemetry import create_llm_operation_span, set_span_error, set_span_success
from ..llm.usage import extract_llm_usage, llm_usage_fields
from .summary_models import SummaryError, SummaryMode, SummaryOutput


logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemma-4-26b-a4b-it"
DEFAULT_MAX_OUTPUT_TOKENS = 1200
SOURCE_TEXT_LIMIT = 60_000
URL_CONTEXT_TOOL = types.Tool(url_context=types.UrlContext())

_client: Any | None = None


def _normalize_urls(source_urls: Sequence[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in source_urls or []:
        url = raw.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _max_output_tokens() -> int:
    raw = (os.environ.get("SUMMARY_MAX_TOKENS") or "").strip()
    if not raw:
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_OUTPUT_TOKENS


def _summary_length_guidance(mode: SummaryMode) -> str:
    if mode == "brief":
        return "Keep the summary compact: 1 short paragraph and 3 to 5 key points."
    return "Write a fuller summary: 2 to 4 short paragraphs and 5 to 7 key points."


def _system_instruction(*, use_url_context: bool) -> str:
    context_rule = (
        "Use the URL context tool to inspect the supplied URLs directly."
        if use_url_context
        else "Use only the provided SOURCE_TEXT."
    )
    return (
        "You are a source-grounded compression engine for an MCP content fetch tool. "
        f"{context_rule} "
        "Preserve named entities, numbers, dates, version numbers, error messages, "
        "code identifiers, URLs, and stated uncertainty. "
        "Do not infer missing facts or fill gaps from world knowledge. "
        "Return a single JSON object that matches the requested schema."
    )


def _build_user_prompt(
    *,
    mode: SummaryMode,
    focus_query: str | None,
    source_urls: Sequence[str] | None,
    source_text: str,
    use_url_context: bool,
) -> str:
    focus = focus_query.strip() if focus_query else "None"
    schema = json.dumps(SummaryOutput.model_json_schema(), ensure_ascii=True)
    parts = [
        "<summary_request>",
        f"<summary_mode>{mode}</summary_mode>",
        f"<focus_query>{focus}</focus_query>",
        f"<guidance>{_summary_length_guidance(mode)}</guidance>",
        "<output>",
        "Return valid JSON only. No markdown fences, no prose wrapper.",
        "</output>",
        "<schema>",
        schema,
        "</schema>",
    ]
    if use_url_context:
        parts.extend(["<source_urls>"])
        for url in source_urls or []:
            parts.append(f"<url>{url}</url>")
        parts.extend(
            [
                "</source_urls>",
                "<instructions>",
                "Use the URL context tool on the URLs above. If retrieval fails, say so "
                "in the limitations instead of guessing.",
                "</instructions>",
            ]
        )
    else:
        parts.extend(
            [
                "<source_text>",
                source_text[:SOURCE_TEXT_LIMIT],
                "</source_text>",
                "<instructions>",
                "Summarize only the provided source text. Do not invent missing details.",
                "</instructions>",
            ]
        )
        if source_urls:
            parts.extend(["<source_urls>"])
            for url in source_urls:
                parts.append(f"<url>{url}</url>")
            parts.append("</source_urls>")
    parts.append("</summary_request>")
    return "\n".join(parts)


def _get_client() -> Any:
    global _client
    if _client is None:
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise SummaryError("GEMINI_API_KEY is required for summary generation")
        _client = genai.Client(api_key=api_key)
    return _client


def _response_text(response: Any) -> str:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        if hasattr(parsed, "model_dump"):
            return json.dumps(parsed.model_dump(), ensure_ascii=True)
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=True)

    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                return part_text.strip()

    raise SummaryError("Gemini response did not contain usable text")


def _strip_json_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _parse_summary(raw: str) -> SummaryOutput:
    cleaned = _strip_json_fences(raw)
    try:
        return SummaryOutput.model_validate_json(cleaned)
    except Exception as exc:
        raise SummaryError(f"Summary response was not valid JSON: {exc}") from exc


def _make_config(
    *, use_url_context: bool, max_output_tokens: int
) -> types.GenerateContentConfig:
    config: dict[str, Any] = {
        "system_instruction": _system_instruction(use_url_context=use_url_context),
        "response_mime_type": "application/json",
        "response_json_schema": SummaryOutput.model_json_schema(),
        "temperature": 0.0,
        "max_output_tokens": max_output_tokens,
    }
    if use_url_context:
        config["tools"] = [URL_CONTEXT_TOOL]
    return types.GenerateContentConfig(**config)


async def _generate_summary(
    *,
    model_id: str,
    source_text: str,
    source_urls: Sequence[str] | None,
    mode: SummaryMode,
    focus_query: str | None,
    use_url_context: bool,
) -> tuple[SummaryOutput, Any | None]:
    client = _get_client()
    config = _make_config(
        use_url_context=use_url_context,
        max_output_tokens=_max_output_tokens(),
    )
    contents = _build_user_prompt(
        mode=mode,
        focus_query=focus_query,
        source_urls=source_urls,
        source_text=source_text,
        use_url_context=use_url_context,
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model_id,
        contents=contents,
        config=config,
    )
    return _parse_summary(_response_text(response)), extract_llm_usage(response)


async def summarize_with_fallback(
    *,
    source_text: str,
    source_urls: Sequence[str] | None,
    mode: SummaryMode,
    focus_query: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    source_urls_list = _normalize_urls(source_urls)
    primary_model = (os.environ.get("SUMMARY_GEMINI_MODEL") or PRIMARY_MODEL).strip()
    fallback_model = (
        os.environ.get("SUMMARY_GEMMA_FALLBACK_MODEL") or FALLBACK_MODEL
    ).strip()
    max_tokens = _max_output_tokens()

    with create_llm_operation_span(
        "summarize",
        system="gemini",
        attributes={
            "gen_ai.request.model": primary_model,
            "summary.mode": mode,
            "summary.focus_query": (focus_query or "")[:500],
            "summary.input_chars": len(source_text),
            "summary.source_url_count": len(source_urls_list),
            "summary.max_tokens": max_tokens,
        },
    ) as span:
        try:
            summary, usage = await _generate_summary(
                model_id=primary_model,
                source_text=source_text,
                source_urls=source_urls_list or None,
                mode=mode,
                focus_query=focus_query,
                use_url_context=bool(source_urls_list),
            )
            backend = "gemini-api"
            model_used = primary_model
        except Exception as primary_exc:
            logger.warning(
                "Gemini summary failed for model %s, trying fallback %s: %s",
                primary_model,
                fallback_model,
                primary_exc,
            )
            try:
                summary, usage = await _generate_summary(
                    model_id=fallback_model,
                    source_text=source_text,
                    source_urls=source_urls_list or None,
                    mode=mode,
                    focus_query=focus_query,
                    use_url_context=False,
                )
                backend = "gemma-fallback"
                model_used = fallback_model
            except Exception as fallback_exc:
                set_span_error(span, fallback_exc)
                raise

    payload = summary.model_dump()
    payload["mode"] = mode
    payload["model"] = model_used
    payload.update(llm_usage_fields(model_used=model_used, usage=usage))
    payload["backend"] = backend
    span.set_attribute("gen_ai.response.model", model_used)
    if usage:
        if usage.input_tokens is not None:
            span.set_attribute("gen_ai.usage.prompt_tokens", usage.input_tokens)
        if usage.output_tokens is not None:
            span.set_attribute("gen_ai.usage.completion_tokens", usage.output_tokens)
        if usage.total_tokens is not None:
            span.set_attribute("gen_ai.usage.total_tokens", usage.total_tokens)
    span.set_attribute("summary.backend", backend)
    span.set_attribute("summary.key_points_count", len(payload.get("key_points", [])))
    span.set_attribute(
        "summary.important_entities_count",
        len(payload.get("important_entities", [])),
    )
    set_span_success(span)
    return payload, model_used, backend
