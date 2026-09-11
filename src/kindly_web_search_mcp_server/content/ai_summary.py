"""AI-powered content summarization using Gemini and Gemma models.

Summarizes already-fetched content: one source per call, trying models in
order (SUMMARY_GEMINI_MODEL -> gemini-3.1-flash-lite -> Gemma) until one
succeeds. Every attempt is logged to an optional rung list for analytics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Sequence

from google import genai  # type: ignore[import-untyped]
from google.genai import types
from pydantic import BaseModel, Field

from ..prompts.builders import anchor_today
from ..telemetry import create_llm_operation_span, set_span_error, set_span_success
from ..telemetry.usage import extract_llm_usage, llm_usage_fields

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemma-4-26b-a4b-it"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
SOURCE_TEXT_LIMIT = 60_000
URL_CONTEXT_TOOL = types.Tool(url_context=types.UrlContext())

_INACCESSIBLE_CLAIM_RE = re.compile(
    r"could not retrieve|unable to access|inaccessible|blocked or inaccessible|"
    r"could not access the content",
    re.IGNORECASE,
)

MODE = "detailed"

BODY_RULE = (
    "Summarize only SOURCE_TEXT. Do not fetch URLs. Do not claim the page is "
    "inaccessible, blocked, or unreadable if SOURCE_TEXT is non-empty. "
    "If SOURCE_TEXT is nav/captions/chrome, say so in limitations."
)
JSON_ONLY_RULE = "Return valid JSON only. No markdown fences, no prose wrapper."


class SummaryError(RuntimeError):
    pass


class SummaryEntity(BaseModel):
    name: str = Field(description="Entity name preserved from the source.")
    type: str = Field(description="Entity type such as person, project, or model.")
    why_relevant: str = Field(
        description="Short explanation of why the entity matters in the source."
    )


class SummaryOutput(BaseModel):
    summary: str = Field(description="Concise source-grounded summary text.")
    key_points: list[str] = Field(default_factory=list, description="Bullet-friendly takeaways.")
    important_entities: list[SummaryEntity] = Field(
        default_factory=list, description="Named entities that matter in the source."
    )
    verbatim_terms: list[str] = Field(
        default_factory=list, description="Important exact terms, identifiers, or URLs."
    )
    limitations: list[str] = Field(
        default_factory=list, description="Any gaps, caveats, or missing context."
    )
    source_date: str | None = Field(
        default=None, description="Publication date found in the source, ISO format."
    )


def summary_stub() -> dict[str, Any]:
    return {
        "mode": MODE,
        "summary": "",
        "key_points": [],
        "important_entities": [],
        "verbatim_terms": [],
        "limitations": ["No source text or URL context was available to summarize."],
    }


def _drop_inaccessible_claim(summary: dict[str, Any], source_text: str) -> dict[str, Any]:
    """Neutralize 'page inaccessible' claims when body text was actually provided."""
    if len(source_text.strip()) < 400:
        return summary
    limitations = summary.get("limitations") or []
    if not isinstance(limitations, list):
        limitations = [str(limitations)]
    blob = f"{summary.get('summary') or ''} {' '.join(str(item) for item in limitations)}"
    if not _INACCESSIBLE_CLAIM_RE.search(blob):
        return summary
    return {
        **summary,
        "summary": "Source text was present but the model failed to summarize it.",
        "key_points": [],
        "limitations": [
            *(str(item) for item in limitations),
            "model_claimed_inaccessible_with_body",
        ],
    }


_client: Any | None = None


def _get_client() -> Any:
    global _client
    if _client is None:
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise SummaryError("GEMINI_API_KEY is required for summary generation")
        _client = genai.Client(api_key=api_key)
    return _client


def _max_output_tokens() -> int:
    raw = (os.environ.get("SUMMARY_MAX_TOKENS") or "").strip()
    if not raw:
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_OUTPUT_TOKENS


def _model_attempts() -> list[tuple[str, str]]:
    """(model_id, backend) pairs in fallback order: primary, secondary, Gemma."""
    primary = (os.environ.get("SUMMARY_GEMINI_MODEL") or PRIMARY_MODEL).strip()
    attempts = [(primary, "gemini-api")]
    if primary != GEMINI_FALLBACK_MODEL:
        attempts.append((GEMINI_FALLBACK_MODEL, "gemini-api-fallback"))
    attempts.append((FALLBACK_MODEL, "gemma-fallback"))
    return attempts


def _rung_provider(model_id: str) -> str:
    return "gemma" if "gemma" in model_id.lower() else "google"


def _log_rung(
    rung_log: list | None,
    *,
    model_used: str,
    outcome: str,
    error_type: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: float | None = None,
    item_index: int = 0,
) -> None:
    """Append one attempt record (row order is stamped by the analytics layer)."""
    if rung_log is None:
        return
    rung_log.append(
        {
            "rung": model_used,
            "provider": _rung_provider(model_used),
            "model_used": model_used,
            "outcome": outcome,
            "error_type": error_type,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "item_index": item_index,
        }
    )


def _summary_length_guidance() -> str:
    return "2 to 4 short paragraphs, 5 to 7 key points."


def _system_instruction(*, use_url_context: bool, model_id: str) -> str:
    context_rule = (
        "Use the URL context tool to inspect the supplied URLs directly."
        if use_url_context
        else BODY_RULE
    )
    return (
        "<role>\n"
        "You are a source-grounded extraction and summarization agent.\n"
        "Your job: read provided content and produce structured summaries,\n"
        "preserving every named entity, number, date, version string, error\n"
        "message, code identifier, URL, and stated uncertainty from the source.\n"
        "</role>\n"
        "\n"
        "<identity>\n"
        f"Model: {model_id}\n"
        "Knowledge cutoff: January 2025\n"
        "Current year: 2026\n"
        "</identity>\n"
        "\n"
        "<rules>\n"
        "- EXTRACT, DON'T INFER: Use only the provided content for facts.\n"
        "  When the source implies a relationship, state it as a deduction\n"
        "  from context. Never invent missing dates, URLs, or statistics.\n"
        "- HANDLE AMBIGUITY: If the source is contradictory or unclear, capture\n"
        "  it in `limitations`, don't guess.\n"
        "- NOISE FILTERING: Ignore navigation, ads, cookie banners, and\n"
        "  boilerplate. Focus on the main content body.\n"
        f"- CONTEXT RULE: {context_rule}\n"
        "- ENTITIES: For each named entity found, capture name, type\n"
        "  (person/org/project/model/term), and why it matters in context.\n"
        "- PRESERVE STRUCTURE: Keep lists, tables, and hierarchical\n"
        "  relationships from the source where possible.\n"
        "</rules>\n"
        "\n"
        "<output>\n"
        "Return a single JSON object matching the requested schema.\n"
        "No markdown, no prose wrapper.\n"
        "</output>"
    )


def _build_user_prompt(
    *,
    focus_query: str | None,
    source_urls: Sequence[str] | None,
    source_text: str,
    use_url_context: bool,
) -> str:
    focus = focus_query.strip() if focus_query else "None"
    schema = json.dumps(SummaryOutput.model_json_schema(), ensure_ascii=True)
    parts = [
        "<summary_request>",
        f"<summary_mode>{MODE}</summary_mode>",
        f"<focus_query>{focus}</focus_query>",
        f"<today>{anchor_today()}</today>",
        "<few_shot_example>",
        "Here is an example of the expected output format:",
        "{",
        '  "summary": "The article announces the release of Python 3.14.0, '
        "highlighting new pattern matching syntax and a 15% performance "
        'improvement over 3.13.",',
        '  "key_points": [',
        '    "Python 3.14.0 released on 2026-10-01",',
        '    "New structural pattern matching features added",',
        '    "15% faster than 3.13 on standard benchmarks",',
        '    "Requires macOS 12+ or glibc 2.35+"',
        "  ],",
        '  "important_entities": [',
        '    {"name": "Python 3.14.0", "type": "software_version", '
        '"why_relevant": "The main subject of the article"},',
        '    {"name": "PSF", "type": "organization", '
        '"why_relevant": "Release authority, the Python Software Foundation"}',
        "  ],",
        '  "verbatim_terms": ["PEP 701", "structural pattern matching", "glibc 2.35"],',
        '  "limitations": ["No benchmark methodology details provided"],',
        '  "source_date": "2026-10-01"',
        "}",
        "That is the exact format. Always match it.",
        "</few_shot_example>",
        "<schema>",
        schema,
        "</schema>",
    ]
    has_body = bool(source_text.strip())
    if use_url_context and not has_body:
        parts.extend(["<source_urls>"])
        for url in source_urls or []:
            parts.append(f"<url>{url}</url>")
        parts.extend(
            [
                "</source_urls>",
                "<instructions>",
                "Use the URL context tool on the URLs above. If retrieval fails, "
                "say so in the limitations instead of guessing.",
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
                BODY_RULE if has_body else "Summarize only the provided source text. Do not invent missing details.",
                "</instructions>",
            ]
        )
        if source_urls:
            parts.extend(["<source_urls>"])
            for url in source_urls:
                parts.append(f"<url>{url}</url>")
            parts.append("</source_urls>")
    inaccessible_constraint = (
        "Do not claim the page is inaccessible, blocked, or unreadable if SOURCE_TEXT is non-empty."
        if has_body
        else "If the source is paywalled, truncated, or inaccessible, note it in limitations."
    )
    # Constraints LAST per Google Gemini 3 prompting guidance:
    # place instructions at end of prompt, after data context.
    parts.extend(
        [
            "<constraints>",
            JSON_ONLY_RULE,
            f"Length: {_summary_length_guidance()}",
            inaccessible_constraint,
            "Do not invent missing details.",
            "</constraints>",
            "</summary_request>",
        ]
    )
    return "\n".join(parts)


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
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                return part_text.strip()

    raise SummaryError("Gemini response did not contain usable text")


def _parse_summary(raw: str) -> SummaryOutput:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return SummaryOutput.model_validate_json(cleaned)
    except Exception as exc:
        raise SummaryError(f"Summary response was not valid JSON: {exc}") from exc


def _make_config(*, use_url_context: bool, model_id: str) -> types.GenerateContentConfig:
    config: dict[str, Any] = {
        "system_instruction": _system_instruction(
            use_url_context=use_url_context, model_id=model_id
        ),
        "response_mime_type": "application/json",
        "response_json_schema": SummaryOutput.model_json_schema(),
        "temperature": 1.0,
        "max_output_tokens": _max_output_tokens(),
    }
    if use_url_context:
        config["tools"] = [URL_CONTEXT_TOOL]
    return types.GenerateContentConfig(**config)


def _finalize_payload(
    summary: SummaryOutput,
    *,
    source_text: str,
    model_id: str,
    backend: str,
    usage: Any | None,
) -> dict[str, Any]:
    """Build the public summary payload with model, backend, and token fields."""
    payload = summary.model_dump()
    payload["mode"] = MODE
    payload["model"] = model_id
    payload["model_used"] = model_id
    payload["backend"] = backend
    payload = _drop_inaccessible_claim(payload, source_text)
    payload.update(llm_usage_fields(model_used=model_id, usage=usage))
    if usage is not None:
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if input_tokens is not None:
            payload["prompt_tokens"] = input_tokens
        completion = output_tokens
        if completion is None and str(payload.get("summary") or "").strip():
            completion = 0
        if completion is not None:
            payload["completion_tokens"] = completion
        payload["provider"] = _rung_provider(model_id)
    return payload


async def summarize(
    source_text: str,
    *,
    ai_summary: bool = False,
    focus_query: str | None = None,
    source_urls: Sequence[str] | None = None,
    rung_log: list | None = None,
    item_index: int = 0,
) -> dict[str, Any] | None:
    """Summarize one already-fetched source, trying each model in order.

    Returns the payload dict, or None when ai_summary is disabled, or a stub
    when there is nothing to summarize.
    """
    if not ai_summary:
        return None
    if not source_text.strip() and not source_urls:
        return summary_stub()

    use_url_context = bool(source_urls) and not source_text.strip()
    with create_llm_operation_span(
        "summarize",
        system="gemini",
        attributes={
            "llm.model_name": _model_attempts()[0][0],
            "summary.mode": MODE,
            "summary.focus_query": (focus_query or "")[:500],
            "summary.input_chars": len(source_text),
            "summary.source_url_count": len(source_urls or []),
            "summary.max_tokens": _max_output_tokens(),
        },
    ) as span:
        last_error: Exception | None = None
        for model_id, backend in _model_attempts():
            _t0 = time.monotonic()
            try:
                contents = _build_user_prompt(
                    focus_query=focus_query,
                    source_urls=source_urls,
                    source_text=source_text,
                    use_url_context=use_url_context and model_id != FALLBACK_MODEL,
                )
                response = await asyncio.to_thread(
                    _get_client().models.generate_content,
                    model=model_id,
                    contents=contents,
                    config=_make_config(
                        use_url_context=use_url_context and model_id != FALLBACK_MODEL,
                        model_id=model_id,
                    ),
                )
                summary = _parse_summary(_response_text(response))
                usage = extract_llm_usage(response)
            except Exception as exc:
                _log_rung(
                    rung_log,
                    model_used=model_id,
                    outcome="error",
                    error_type=type(exc).__name__,
                    latency_ms=(time.monotonic() - _t0) * 1000.0,
                    item_index=item_index,
                )
                last_error = exc
                logger.warning("Summary failed on %s: %s", model_id, exc)
                continue
            _log_rung(
                rung_log,
                model_used=model_id,
                outcome="success",
                input_tokens=getattr(usage, "input_tokens", None) if usage else None,
                output_tokens=getattr(usage, "output_tokens", None) if usage else None,
                latency_ms=(time.monotonic() - _t0) * 1000.0,
                item_index=item_index,
            )
            payload = _finalize_payload(
                summary,
                source_text=source_text,
                model_id=model_id,
                backend=backend,
                usage=usage,
            )
            if usage is not None:
                if usage.input_tokens is not None:
                    span.set_attribute("llm.token_count.prompt", usage.input_tokens)
                if usage.output_tokens is not None:
                    span.set_attribute("llm.token_count.completion", usage.output_tokens)
            span.set_attribute("llm.model_name", model_id)
            span.set_attribute("summary.backend", backend)
            span.set_attribute("summary.key_points_count", len(payload.get("key_points", [])))
            span.set_attribute(
                "summary.important_entities_count",
                len(payload.get("important_entities", [])),
            )
            set_span_success(span)
            return payload
        error = SummaryError(f"All summary models failed: {last_error}")
        set_span_error(span, error)
        raise error


async def summarize_batch(
    items: Sequence[dict[str, Any]],
    *,
    ai_summary: bool = False,
    focus_query: str | None = None,
    max_concurrency: int = 4,
    rung_log: list | None = None,
) -> list[dict[str, Any] | None]:
    """Summarize many items with bounded concurrency; failures get a stub."""
    if not ai_summary:
        return [None for _ in items]
    if not items:
        return []
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(item_index: int, item: dict[str, Any]) -> dict[str, Any]:
        url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        if not url:
            return summary_stub()
        try:
            async with semaphore:
                payload = await summarize(
                    str(item.get("page_content") or ""),
                    ai_summary=True,
                    focus_query=focus_query,
                    source_urls=[url],
                    rung_log=rung_log,
                    item_index=item_index,
                )
            return payload if payload is not None else summary_stub()
        except SummaryError:
            return summary_stub()

    return list(await asyncio.gather(*(_one(i, item) for i, item in enumerate(items))))


__all__ = [
    "SummaryEntity",
    "SummaryError",
    "SummaryOutput",
    "summarize",
    "summarize_batch",
    "summary_stub",
]
