from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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

_INACCESSIBLE_CLAIM_RE = re.compile(
    r"could not retrieve|unable to access|inaccessible|blocked or inaccessible|"
    r"could not access the content",
    re.IGNORECASE,
)
_PROVIDER_BY_BACKEND = {
    "gemini-api": "google",
    "gemini-api-fallback": "google",
    "gemini-batch-api": "google",
    "gemini-per-item-fallback": "google",
    "gemma-fallback": "gemma",
    "gemma-batch-fallback": "gemma",
}


def _drop_inaccessible_claim(summary: dict[str, Any], source_text: str) -> dict[str, Any]:
    if len(source_text.strip()) < 400:
        return summary
    limitations = summary.get("limitations") or []
    if not isinstance(limitations, list):
        limitations = [str(limitations)]
    blob = f"{summary.get('summary') or ''} {' '.join(str(item) for item in limitations)}"
    if not _INACCESSIBLE_CLAIM_RE.search(blob):
        return summary
    updated = dict(summary)
    updated["summary"] = "Source text was present but the model failed to summarize it."
    updated["key_points"] = []
    updated["limitations"] = [*(str(item) for item in limitations), "model_claimed_inaccessible_with_body"]
    return updated


def _attach_token_fields(
    payload: dict[str, Any], usage: Any | None, backend: str
) -> dict[str, Any]:
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    if input_tokens is not None:
        payload["input_tokens"] = input_tokens
        payload["prompt_tokens"] = input_tokens
    completion = output_tokens
    if completion is None and str(payload.get("summary") or "").strip():
        completion = 0
    if output_tokens is not None:
        payload["output_tokens"] = output_tokens
    if completion is not None:
        payload["completion_tokens"] = completion
    provider = _PROVIDER_BY_BACKEND.get(backend)
    if provider:
        payload["provider"] = provider
    return payload


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
    body_rule = (
        "Summarize only SOURCE_TEXT. Do not fetch URLs. Do not claim the page is "
        "inaccessible, blocked, or unreadable if SOURCE_TEXT is non-empty. "
        "If SOURCE_TEXT is nav/captions/chrome, say so in limitations."
    )
    context_rule = (
        "Use the URL context tool to inspect the supplied URLs directly."
        if use_url_context
        else body_rule
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
        body_instructions = (
            "Summarize only SOURCE_TEXT. Do not fetch URLs. Do not claim the page is "
            "inaccessible, blocked, or unreadable if SOURCE_TEXT is non-empty. "
            "If SOURCE_TEXT is nav/captions/chrome, say so in limitations."
            if has_body
            else "Summarize only the provided source text. Do not invent missing details."
        )
        parts.extend(
            [
                "<source_text>",
                source_text[:SOURCE_TEXT_LIMIT],
                "</source_text>",
                "<instructions>",
                body_instructions,
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
            "Return valid JSON only. No markdown fences, no prose wrapper.",
            f"Length: {_summary_length_guidance()}",
            inaccessible_constraint,
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
    items: Sequence[dict[str, Any]],
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
        "<items>",
    ]
    for item in items:
        url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url") or ""
        text = str(item.get("page_content") or "")[:SOURCE_TEXT_LIMIT]
        parts.extend(
            [
                "<item>",
                f"<url>{url}</url>",
                "<source_text>",
                text,
                "</source_text>",
                "</item>",
            ]
        )
    parts.extend(
        [
            "</items>",
            "<instructions>",
            "Summarize only SOURCE_TEXT. Do not fetch URLs. Do not claim the page is "
            "inaccessible, blocked, or unreadable if SOURCE_TEXT is non-empty. "
            "If SOURCE_TEXT is nav/captions/chrome, say so in limitations.",
            "Return a JSON object matching the schema below with one summary entry for every item.",
            "Each entry must include the exact URL it corresponds to in the 'url' field.",
            "Do not invent missing details.",
            "</instructions>",
            "<schema>",
            schema,
            "</schema>",
            "<constraints>",
            "Return valid JSON only. No markdown fences, no prose wrapper.",
            "Preserve every named entity, number, date, version string, error message, "
            "code identifier, URL, and stated uncertainty from each source.",
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
        "system_instruction": _system_instruction(use_url_context=False, model_id=model_id),
        "response_mime_type": "application/json",
        "temperature": 1.0,
        "max_output_tokens": max_output_tokens,
    }
    if use_schema:
        config["response_json_schema"] = BatchSummaryOutput.model_json_schema()
    return types.GenerateContentConfig(**config)


async def _generate_batch_summary(
    *,
    model_id: str,
    items: Sequence[dict[str, Any]],
    mode: SummaryMode,
    focus_query: str | None,
) -> tuple[Any, Any | None]:
    client = _get_batch_client()
    max_output_tokens = _max_output_tokens()
    scaled_max = min(max_output_tokens * max(len(items), 1), 12_000)
    config = _make_batch_config(max_output_tokens=scaled_max, model_id=model_id)
    contents = _build_batch_user_prompt(
        mode=mode,
        focus_query=focus_query,
        items=items,
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
    max_concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Summarize many URLs in a single Gemini call using GEMINI_SECOND_API_KEY.

    Items with a non-empty body go through one batched SOURCE_TEXT call; items
    without a body are summarized per-item (URL-context enabled there).
    Falls back to per-item summaries on the primary GEMINI_API_KEY if the batch call fails.
    """
    with_body = [item for item in items if str(item.get("page_content") or "").strip()]
    without_body = [item for item in items if not str(item.get("page_content") or "").strip()]
    if not with_body:
        return list(await _fallback_per_item_summaries(items, mode=mode, focus_query=focus_query, max_concurrency=max_concurrency))

    urls = [
        item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        for item in with_body
    ]
    urls = [url for url in urls if url]
    if not urls:
        return list(await _fallback_per_item_summaries(items, mode=mode, focus_query=focus_query, max_concurrency=max_concurrency))

    def _reorder(batched: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Interleave batched results back into the original item order."""
        out: list[dict[str, Any]] = []
        cursor = 0
        for item in items:
            if str(item.get("page_content") or "").strip():
                out.append(batched[cursor])
                cursor += 1
            else:
                out.append(summary_stub(mode))
        return out

    empty_summaries = await _fallback_per_item_summaries(
        without_body, mode=mode, focus_query=focus_query, max_concurrency=max_concurrency
    )
    try:
        batched = await _summarize_batched(
            with_body, mode=mode, focus_query=focus_query
        )
    except Exception:
        batched = await _fallback_per_item_summaries(
            with_body, mode=mode, focus_query=focus_query, max_concurrency=max_concurrency
        )
    combined = _reorder(batched)
    empty_iter = iter(empty_summaries)
    return [
        next(empty_iter) if not str(item.get("page_content") or "").strip() else combined[index]
        for index, item in enumerate(items)
    ]


async def _summarize_batched(
    items: Sequence[dict[str, Any]],
    *,
    mode: SummaryMode,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    urls = [
        item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        for item in items
    ]
    urls = [url for url in urls if url]
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
                    items=items,
                    mode=mode,
                    focus_query=focus_query,
                )
                backend = "gemini-batch-api"
                raw_text = _response_text(response)
                batch = _parse_batch_summary(raw_text)
                mapped = _map_batch_summaries(
                    items,
                    batch.summaries,
                    mode=mode,
                    model_id=model_id,
                    backend=backend,
                )
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
            scaled_max = min(max_output_tokens * max(len(items), 1), 12_000)
            config = _make_batch_config(
                max_output_tokens=scaled_max, model_id=fallback_model, use_schema=False
            )
            contents = _build_batch_user_prompt(
                mode=mode, focus_query=focus_query, items=items
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
                items,
                batch.summaries,
                mode=mode,
                model_id=fallback_model,
                backend=backend,
            )
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




async def _fallback_per_item_summaries(
    items: Sequence[dict[str, Any]],
    *,
    mode: SummaryMode,
    focus_query: str | None,
    max_concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Run per-item summaries on the primary GEMINI_API_KEY with bounded concurrency."""
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _per_item_summary(item, mode=mode, focus_query=focus_query)

    return list(await asyncio.gather(*(_bounded(item) for item in items)))


def _map_batch_summaries(
    items: Sequence[dict[str, Any]],
    summaries: Sequence[Any],
    mode: SummaryMode,
    model_id: str,
    backend: str = "gemini-batch-api",
) -> list[dict[str, Any]]:
    """Map returned summaries back to original items by URL, preserving order for missing entries."""
    by_url: dict[str, dict[str, Any]] = {}
    for entry in summaries:
        url = getattr(entry, "url", None)
        if not url:
            continue
        payload = {
            **entry.model_dump(),
            "mode": mode,
            "model": model_id,
            "model_used": model_id,
            "backend": backend,
        }
        by_url[url] = payload
    results: list[dict[str, Any]] = []
    for item in items:
        url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
        source_text = str(item.get("page_content") or "")
        if url and url in by_url:
            payload = _drop_inaccessible_claim(by_url[url], source_text)
            results.append(payload)
        else:
            stub = summary_stub(mode)
            stub["limitations"] = ["No summary returned for this URL in the batch response."]
            results.append(stub)
    return results




async def _per_item_summary(
    item: dict[str, Any],
    *,
    mode: SummaryMode,
    focus_query: str | None,
) -> dict[str, Any]:
    source_url = item.get("fetched_url") or item.get("normalized_url") or item.get("input_url")
    if not source_url:
        return summary_stub(mode)

    content_text = str(item.get("page_content") or "")
    has_body = bool(content_text.strip())
    for model_id in _summary_model_chain():
        try:
            summary, usage = await _generate_summary(
                model_id=model_id,
                source_text=content_text[:SOURCE_TEXT_LIMIT],
                source_urls=[source_url],
                mode=mode,
                focus_query=focus_query,
                use_url_context=not has_body,
                client=_get_client(),
            )
            payload = summary.model_dump()
            payload["mode"] = mode
            payload["model"] = model_id
            payload["model_used"] = model_id
            payload["backend"] = "gemini-per-item-fallback"
            payload = _drop_inaccessible_claim(payload, content_text)
            return _attach_token_fields(payload, usage, "gemini-per-item-fallback")
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
                    use_url_context=bool(source_urls_list) and not source_text.strip(),
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
    payload["model_used"] = model_used
    payload["backend"] = backend
    payload = _drop_inaccessible_claim(payload, source_text)
    payload = _attach_token_fields(payload, usage, backend)
    payload.update(llm_usage_fields(model_used=model_used, usage=usage))
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
