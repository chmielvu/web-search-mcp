from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Sequence

from google import genai  # type: ignore[import-untyped]
from google.genai import types

from ..telemetry import create_llm_operation_span, set_span_error, set_span_success
from ..telemetry.usage import extract_llm_usage, llm_usage_fields
from .summary_models import SummaryError, SummaryMode, SummaryOutput, summary_stub


logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"
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


def _summary_model_chain() -> tuple[str, ...]:
    primary = (os.environ.get("SUMMARY_GEMINI_MODEL") or PRIMARY_MODEL).strip()
    return tuple(dict.fromkeys((primary, GEMINI_FALLBACK_MODEL)))


def _summary_length_guidance() -> str:
    return "2 to 4 short paragraphs, 5 to 7 key points."


def _system_instruction(*, use_url_context: bool, model_id: str = PRIMARY_MODEL) -> str:
    context_rule = (
        "Use the URL context tool to inspect the supplied URLs directly."
        if use_url_context
        else "Use only the provided SOURCE_TEXT."
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
    mode: SummaryMode,
    focus_query: str | None,
    source_urls: Sequence[str] | None,
    source_text: str,
    use_url_context: bool,
) -> str:
    from ..prompts.builders import anchor_today

    focus = focus_query.strip() if focus_query else "None"
    schema = json.dumps(SummaryOutput.model_json_schema(), ensure_ascii=True)
    parts = [
        "<summary_request>",
        f"<summary_mode>{mode}</summary_mode>",
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
    if use_url_context:
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
                "Summarize only the provided source text. Do not invent missing details.",
                "</instructions>",
            ]
        )
        if source_urls:
            parts.extend(["<source_urls>"])
            for url in source_urls:
                parts.append(f"<url>{url}</url>")
            parts.append("</source_urls>")
    # Constraints LAST per Google Gemini 3 prompting guidance:
    # place instructions at end of prompt, after data context.
    parts.extend(
        [
            "<constraints>",
            "Return valid JSON only. No markdown fences, no prose wrapper.",
            f"Length: {_summary_length_guidance()}",
            "If the source is paywalled, truncated, or inaccessible, note it in limitations.",
            "Do not invent missing details.",
            "</constraints>",
            "</summary_request>",
        ]
    )
    return "\n".join(parts)


def _get_client() -> Any:
    global _client
    if _client is None:
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise SummaryError("GEMINI_API_KEY is required for summary generation")
        _client = genai.Client(api_key=api_key)
    return _client


_batch_client: Any | None = None


def _get_batch_client() -> Any:
    """Dedicated client for batch summaries, using the paid GEMINI_SECOND_API_KEY."""
    global _batch_client
    if _batch_client is None:
        api_key = (os.environ.get("GEMINI_SECOND_API_KEY") or "").strip()
        if not api_key:
            raise SummaryError("GEMINI_SECOND_API_KEY is required for batch summary generation")
        _batch_client = genai.Client(api_key=api_key)
    return _batch_client


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
    *, use_url_context: bool, max_output_tokens: int, model_id: str = PRIMARY_MODEL
) -> types.GenerateContentConfig:
    config: dict[str, Any] = {
        "system_instruction": _system_instruction(
            use_url_context=use_url_context, model_id=model_id
        ),
        "response_mime_type": "application/json",
        "response_json_schema": SummaryOutput.model_json_schema(),
        "temperature": 1.0,
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
    client: Any | None = None,
) -> tuple[SummaryOutput, Any | None]:
    client_obj: Any = client or _get_client()
    config = _make_config(
        use_url_context=use_url_context,
        max_output_tokens=_max_output_tokens(),
        model_id=model_id,
    )
    contents = _build_user_prompt(
        mode=mode,
        focus_query=focus_query,
        source_urls=source_urls,
        source_text=source_text,
        use_url_context=use_url_context,
    )
    response = await asyncio.to_thread(
        client_obj.models.generate_content,
        model=model_id,
        contents=contents,
        config=config,
    )
    return _parse_summary(_response_text(response)), extract_llm_usage(response)


def _build_batch_user_prompt(
    *,
    mode: SummaryMode,
    focus_query: str | None,
    source_urls: Sequence[str],
) -> str:
    from ..prompts.builders import anchor_today
    from .summary_models import BatchSummaryOutput

    focus = focus_query.strip() if focus_query else "None"
    schema = json.dumps(BatchSummaryOutput.model_json_schema(), ensure_ascii=True)
    parts = [
        "<batch_summary_request>",
        f"<summary_mode>{mode}</summary_mode>",
        f"<focus_query>{focus}</focus_query>",
        f"<today>{anchor_today()}</today>",
        f"<summary_length>{_summary_length_guidance()}</summary_length>",
        "<source_urls>",
    ]
    for url in source_urls:
        parts.append(f"<url>{url}</url>")
    parts.extend(
        [
            "</source_urls>",
            "<instructions>",
            "Use the URL context tool on ALL of the URLs above.",
            "Return a JSON object matching the schema below with one summary entry for every URL in the list above.",
            "Each entry must include the exact URL it corresponds to in the 'url' field.",
            "Do not invent missing details; note any inaccessible or truncated URLs in limitations.",
            "</instructions>",
            "<schema>",
            schema,
            "</schema>",
            "<constraints>",
            "Return valid JSON only. No markdown fences, no prose wrapper.",
            "Preserve every named entity, number, date, version string, error message, code identifier, URL, and stated uncertainty from each source.",
            "</constraints>",
            "</batch_summary_request>",
        ]
    )
    return "\n".join(parts)


def _make_batch_config(
    *, max_output_tokens: int, model_id: str = PRIMARY_MODEL, use_schema: bool = True
) -> types.GenerateContentConfig:
    from .summary_models import BatchSummaryOutput

    config: dict[str, Any] = {
        "system_instruction": _system_instruction(use_url_context=True, model_id=model_id),
        "response_mime_type": "application/json",
        "temperature": 1.0,
        "max_output_tokens": max_output_tokens,
        "tools": [URL_CONTEXT_TOOL],
    }
    if use_schema:
        config["response_json_schema"] = BatchSummaryOutput.model_json_schema()
    return types.GenerateContentConfig(**config)


async def _generate_batch_summary(
    *,
    model_id: str,
    source_urls: Sequence[str],
    mode: SummaryMode,
    focus_query: str | None,
) -> tuple[Any, Any | None]:
    client = _get_batch_client()
    max_output_tokens = _max_output_tokens()
    # Scale output budget with batch size, but cap at a reasonable limit.
    scaled_max = min(max_output_tokens * max(len(source_urls), 1), 12_000)
    config = _make_batch_config(max_output_tokens=scaled_max, model_id=model_id)
    contents = _build_batch_user_prompt(
        mode=mode,
        focus_query=focus_query,
        source_urls=source_urls,
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model_id,
        contents=contents,
        config=config,
    )
    return response, extract_llm_usage(response)


async def summarize_batch_with_fallback(
    *,
    items: Sequence[dict[str, Any]],
    mode: SummaryMode,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Summarize many URLs in a single Gemini call using GEMINI_SECOND_API_KEY.

    Falls back to per-item summaries on the primary GEMINI_API_KEY if the batch call fails.
    """
    urls = [
        item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        for item in items
    ]
    urls = [url for url in urls if url]
    if not urls:
        return [summary_stub(mode) for _ in items]

    model_chain = _summary_model_chain()
    primary_model = model_chain[0]

    with create_llm_operation_span(
        "summarize_batch",
        system="gemini",
        attributes={
            "llm.model_name": primary_model,
            "summary.mode": mode,
            "summary.focus_query": (focus_query or "")[:500],
            "summary.source_url_count": len(urls),
            "summary.batch": True,
        },
    ) as span:
        last_error: Exception | None = None
        for model_id in model_chain:
            try:
                response, usage = await _generate_batch_summary(
                    model_id=model_id,
                    source_urls=urls,
                    mode=mode,
                    focus_query=focus_query,
                )
                backend = "gemini-batch-api"
                raw_text = _response_text(response)
                batch = _parse_batch_summary(raw_text)
                mapped = _map_batch_summaries(items, batch.summaries, mode=mode, model_id=model_id)
                span.set_attribute("llm.model_name", model_id)
                if usage:
                    if usage.input_tokens is not None:
                        span.set_attribute("llm.token_count.prompt", usage.input_tokens)
                    if usage.output_tokens is not None:
                        span.set_attribute("llm.token_count.completion", usage.output_tokens)
                    if usage.total_tokens is not None:
                        span.set_attribute("llm.token_count.total", usage.total_tokens)
                span.set_attribute("summary.backend", backend)
                span.set_attribute("summary.batch_size", len(items))
                span.set_attribute("summary.returned_summaries", len(mapped))
                set_span_success(span)
                return mapped
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Batch summary failed on %s, trying next summary tier: %s",
                    model_id,
                    exc,
                )

        if last_error is not None:
            set_span_error(span, last_error)
        # Try Gemma as a batch model before falling back to per-item summaries.
        fallback_model = (os.environ.get("SUMMARY_GEMMA_FALLBACK_MODEL") or FALLBACK_MODEL).strip()
        try:
            # Gemma does not support response_json_schema; call with use_schema=False.
            client = _get_batch_client()
            max_output_tokens = _max_output_tokens()
            scaled_max = min(max_output_tokens * max(len(urls), 1), 12_000)
            config = _make_batch_config(
                max_output_tokens=scaled_max, model_id=fallback_model, use_schema=False
            )
            contents = _build_batch_user_prompt(
                mode=mode, focus_query=focus_query, source_urls=urls
            )
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=fallback_model,
                contents=contents,
                config=config,
            )
            usage = extract_llm_usage(response)
            backend = "gemma-batch-fallback"
            raw_text = _response_text(response)
            batch = _parse_batch_summary(raw_text)
            mapped = _map_batch_summaries(
                items, batch.summaries, mode=mode, model_id=fallback_model
            )
            span.set_attribute("llm.model_name", fallback_model)
            span.set_attribute("summary.backend", backend)
            span.set_attribute("summary.batch_size", len(items))
            span.set_attribute("summary.returned_summaries", len(mapped))
            set_span_success(span)
            return mapped
        except Exception as exc:
            logger.warning("Batch summary also failed on Gemma fallback: %s", exc)
            if last_error is not None:
                set_span_error(span, last_error)
        return await _fallback_per_item_summaries(items, mode=mode, focus_query=focus_query)


def _parse_batch_summary(raw: str) -> Any:
    from .summary_models import BatchSummaryOutput

    cleaned = _strip_json_fences(raw)
    try:
        return BatchSummaryOutput.model_validate_json(cleaned)
    except Exception as exc:
        raise SummaryError(f"Batch summary response was not valid JSON: {exc}") from exc


def _map_batch_summaries(
    items: Sequence[dict[str, Any]],
    summaries: Sequence[Any],
    *,
    mode: SummaryMode,
    model_id: str,
) -> list[dict[str, Any]]:
    """Map returned summaries back to original items by URL, preserving order for missing entries."""
    by_url: dict[str, dict[str, Any]] = {}
    for entry in summaries:
        url = getattr(entry, "url", None)
        if not url:
            continue
        by_url[url] = {
            **entry.model_dump(),
            "mode": mode,
            "model": model_id,
            "model_used": model_id,
            "backend": "gemini-batch-api",
        }

    results: list[dict[str, Any]] = []
    for item in items:
        url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        if url and url in by_url:
            results.append(by_url[url])
        else:
            stub = summary_stub(mode)
            stub["limitations"] = ["No summary returned for this URL in the batch response."]
            results.append(stub)
    return results


async def _fallback_per_item_summaries(
    items: Sequence[dict[str, Any]],
    *,
    mode: SummaryMode,
    focus_query: str | None,
) -> list[dict[str, Any]]:
    """Run per-item summaries using the paid GEMINI_SECOND_API_KEY client."""
    return await asyncio.gather(
        *(_per_item_summary(item, mode=mode, focus_query=focus_query) for item in items)
    )


async def _per_item_summary(
    item: dict[str, Any],
    *,
    mode: SummaryMode,
    focus_query: str | None,
) -> dict[str, Any]:
    source_url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
    if not source_url:
        return summary_stub(mode)

    content_text = item.get("page_content") or ""
    for model_id in _summary_model_chain():
        try:
            summary, _ = await _generate_summary(
                model_id=model_id,
                source_text=content_text[:30_000],
                source_urls=[source_url],
                mode=mode,
                focus_query=focus_query,
                use_url_context=True,
                client=_get_batch_client(),
            )
            payload = summary.model_dump()
            payload["mode"] = mode
            payload["model"] = model_id
            payload["model_used"] = model_id
            payload["backend"] = "gemini-batch-api"
            return payload
        except Exception as exc:
            logger.warning(
                "Per-item batch summary failed for %s on %s: %s",
                source_url,
                model_id,
                exc,
            )
    return summary_stub(mode)


async def summarize_with_fallback(
    *,
    source_text: str,
    source_urls: Sequence[str] | None,
    mode: SummaryMode,
    focus_query: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    source_urls_list = _normalize_urls(source_urls)
    model_chain = _summary_model_chain()
    primary_model = model_chain[0]
    fallback_model = (os.environ.get("SUMMARY_GEMMA_FALLBACK_MODEL") or FALLBACK_MODEL).strip()
    max_tokens = _max_output_tokens()

    with create_llm_operation_span(
        "summarize",
        system="gemini",
        attributes={
            "llm.model_name": primary_model,
            "summary.mode": mode,
            "summary.focus_query": (focus_query or "")[:500],
            "summary.input_chars": len(source_text),
            "summary.source_url_count": len(source_urls_list),
            "summary.max_tokens": max_tokens,
        },
    ) as span:
        summary: SummaryOutput | None = None
        usage: Any | None = None
        model_used = primary_model
        backend = "gemini-api"
        for index, model_id in enumerate(model_chain):
            try:
                summary, usage = await _generate_summary(
                    model_id=model_id,
                    source_text=source_text,
                    source_urls=source_urls_list or None,
                    mode=mode,
                    focus_query=focus_query,
                    use_url_context=bool(source_urls_list),
                )
                model_used = model_id
                backend = "gemini-api" if index == 0 else "gemini-api-fallback"
                break
            except Exception as exc:
                logger.warning(
                    "Gemini summary failed for model %s: %s",
                    model_id,
                    exc,
                )

        if summary is None:
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

    assert summary is not None
    payload = summary.model_dump()
    payload["mode"] = mode
    payload["model"] = model_used
    payload.update(llm_usage_fields(model_used=model_used, usage=usage))
    payload["backend"] = backend
    span.set_attribute("llm.model_name", model_used)
    if usage:
        if usage.input_tokens is not None:
            span.set_attribute("llm.token_count.prompt", usage.input_tokens)
        if usage.output_tokens is not None:
            span.set_attribute("llm.token_count.completion", usage.output_tokens)
        if usage.total_tokens is not None:
            span.set_attribute("llm.token_count.total", usage.total_tokens)
    span.set_attribute("summary.backend", backend)
    span.set_attribute("summary.key_points_count", len(payload.get("key_points", [])))
    span.set_attribute(
        "summary.important_entities_count",
        len(payload.get("important_entities", [])),
    )
    set_span_success(span)
    return payload, model_used, backend
