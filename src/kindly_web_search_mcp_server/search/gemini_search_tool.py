"""Gemini Search MCP Tool — Google Search grounding for the gemini_search tool.

This module implements the dedicated gemini_search MCP tool, which provides
AI-synthesized answers with inline citations via Gemini + Google Search grounding.

Key features:
- Dual-prompt parallel mode: overview (breadth) + deepdive (precision) via asyncio.gather
- Hardcoded fallback tier: Gemini 3.1 -> 2.5 flash -> 2.5 flash-lite
- Native structured output via response_json_schema on Gemini 3.x
- url_citation annotation extraction from model output content blocks
- Config per Google Gemini 3 docs: temperature=1.0, thinking_level=high

Google grounding: https://ai.google.dev/gemini-api/docs/google-search
Gemini 3 prompting: https://ai.google.dev/gemini-api/docs/gemini-3
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from ..prompts.provider_gemini import build_provider_gemini_prompt, build_dual_prompt
from ..llm.usage import extract_llm_usage
from ..settings import settings
from ..telemetry import create_llm_operation_span, set_span_error, set_span_success

logger = logging.getLogger(__name__)
_gemini_clients: dict[str, Any] = {}
_genai_module: Any | None = None
_genai_types: Any | None = None

# Gemini 3.1 supports grounding + structured output natively.
# 2.5 models serve as fallback for the known 3.1 grounding_chunks null bug.
GEMINI_GROUNDING_TIER = [
    "gemini-3.1-flash-lite",  # PRIMARY — Gemini 3.x grounding + structured output
    "gemini-2.5-flash",        # FAST FALLBACK — best 2.x grounding quality
    "gemini-2.5-flash-lite",   # LAST-RESORT FALLBACK
]


def get_system_prompt(research_goal: str | None = None) -> str:
    """System prompt for Gemini grounding."""
    return build_provider_gemini_prompt(
        query="",
        research_goal=research_goal
        or "Provide thorough, factual answers based on current information",
        provider_name="gemini",
    )[0]


class GeminiSource(BaseModel):
    """Structured source item for Gemini JSON output."""

    url: str = Field(description="Source URL")
    title: str | None = Field(default=None, description="Source title")


class GeminiResearchOutput(BaseModel):
    """Structured research output schema for Gemini grounding."""

    executive_summary: str = Field(description="Brief 1-2 sentence summary")
    key_findings: list[str] = Field(description="Main findings with citations")
    sources: list[GeminiSource] = Field(description="Source URLs and titles")
    confidence: str = Field(description="high/medium/low")
    uncertainties: list[str] | None = Field(default=None)


class GeminiGroundingResult(BaseModel):
    """Result from Gemini Google Search grounding."""

    query: str = Field(description="Original search query")
    answer: str = Field(description="Generated answer text")
    thoughts: str | None = Field(
        default=None, description="Internal reasoning if available"
    )
    structured_result: dict[str, Any] | None = Field(
        default=None, description="Parsed structured output"
    )
    model_used: str = Field(description="Model ID used for generation")
    input_tokens: int | None = Field(default=None, description="Input token count")
    output_tokens: int | None = Field(default=None, description="Output token count")
    structured_output: bool = Field(
        description="Whether structured output was requested"
    )
    web_search_queries: list[str] = Field(
        default_factory=list, description="Queries sent to Google Search"
    )
    grounding_chunks: list[dict[str, str]] = Field(
        default_factory=list, description="Source chunks with URL and title"
    )
    grounding_supports: list[dict[str, Any]] = Field(
        default_factory=list, description="Segment-to-source mappings"
    )
    url_citations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Inline url_citation annotations from model output",
    )
    search_widget_html: str | None = Field(
        default=None, description="Search widget HTML for display (opt-in)"
    )
    fallback_chain: list[str] = Field(
        default_factory=list, description="Models tried during fallback"
    )
    fallback_reason: str | None = Field(
        default=None, description="Reason for fallback if occurred"
    )
    error: str | None = Field(default=None, description="Error message if failed")


def _get_genai_module() -> Any:
    global _genai_module
    if _genai_module is None:
        from google import genai
        _genai_module = genai
    return _genai_module


def _get_genai_types() -> Any:
    global _genai_types
    if _genai_types is None:
        from google.genai import types
        _genai_types = types
    return _genai_types


def _api_key_for_model(model_id: str) -> str:
    if model_id == GEMINI_GROUNDING_TIER[-1]:
        return settings.gemini_second_api_key
    return settings.gemini_api_key


def get_gemini_client(api_key: str | None = None) -> Any | None:
    """Lazy-init Gemini client, cached per API key."""
    resolved_api_key = (api_key or settings.gemini_api_key).strip()
    if not resolved_api_key:
        return None
    client = _gemini_clients.get(resolved_api_key)
    if client is None:
        genai = _get_genai_module()
        client = genai.Client(api_key=resolved_api_key)
        _gemini_clients[resolved_api_key] = client
    return client


def _classify_gemini_error(exc: Exception) -> tuple[str, bool, bool]:
    """Classify Gemini API error.

    Returns:
        (error_type, should_fallback, should_retry)
    """
    status_code = getattr(exc, "status_code", getattr(exc, "code", None))

    if status_code:
        if status_code == 429:
            return ("rate_limit", True, True)
        elif status_code in (503, 502, 504):
            return ("service_unavailable", True, False)
        elif status_code == 404:
            return ("model_not_found", True, False)
        elif status_code == 403:
            return ("permission_denied", True, False)

    error_msg = str(exc).lower()
    if "rate limit" in error_msg or "quota" in error_msg:
        return ("rate_limit", True, True)
    elif "unavailable" in error_msg or "timeout" in error_msg:
        return ("service_unavailable", True, False)
    elif "not found" in error_msg or "does not exist" in error_msg:
        return ("model_not_found", True, False)

    return ("unknown", True, False)


def _is_gemini_model(model_id: str) -> bool:
    """Check if model is a Gemini model (accepts system_instruction)."""
    return model_id.startswith("gemini")


def _is_gemini3_model(model_id: str) -> bool:
    """Check if model is Gemini 3.x (supports grounding + structured output)."""
    return model_id.startswith("gemini-3")


def _build_grounding_config(
    *, structured_output: bool, model_id: str
) -> dict[str, Any]:
    """Build GenerateContentConfig dict for a grounding call.

    Unified config per Google Gemini 3 best practices:
    - temperature=1.0 (mandatory for Gemini 3, safe for 2.5)
    - thinking_level=high (maximizes reasoning for query decomposition + synthesis)
    - Native response_json_schema on Gemini 3.x, text-append fallback on 2.5
    """
    types = _get_genai_types()
    config_dict: dict[str, Any] = {
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": 1.0,
        "max_output_tokens": 8192,
    }

    if _is_gemini3_model(model_id):
        config_dict["thinking_config"] = types.ThinkingConfig(thinking_level="high")

        if structured_output:
            config_dict["response_mime_type"] = "application/json"
            config_dict["response_json_schema"] = (
                GeminiResearchOutput.model_json_schema()
            )

    return config_dict


def _extract_url_citations(response: Any) -> list[dict[str, Any]]:
    """Extract url_citation annotations from model output content blocks.

    The official Gemini grounding API returns citations as annotations on text
    content blocks, each with start_index, end_index, url, and title.
    """
    citations: list[dict[str, Any]] = []
    try:
        if not response.candidates:
            return citations
        for candidate in response.candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                annotations = getattr(part, "annotations", None) or []
                for ann in annotations:
                    ann_type = getattr(ann, "type", None)
                    if ann_type == "url_citation":
                        citations.append({
                            "url": getattr(ann, "url", ""),
                            "title": getattr(ann, "title", ""),
                            "start_index": getattr(ann, "start_index", None),
                            "end_index": getattr(ann, "end_index", None),
                        })
    except Exception:
        pass
    return citations


def _extract_grounding_metadata(response: Any) -> tuple[
    list[str],
    list[dict[str, str]],
    list[dict[str, Any]],
    str | None,
]:
    """Extract grounding metadata from Gemini API response.

    Returns:
        (web_search_queries, grounding_chunks, grounding_supports, search_widget_html)
    """
    web_search_queries: list[str] = []
    grounding_chunks: list[dict[str, str]] = []
    grounding_supports: list[dict[str, Any]] = []
    search_widget_html: str | None = None

    try:
        if not response.candidates:
            return web_search_queries, grounding_chunks, grounding_supports, search_widget_html
        metadata = response.candidates[0].grounding_metadata
        if not metadata:
            return web_search_queries, grounding_chunks, grounding_supports, search_widget_html

        web_search_queries = list(metadata.web_search_queries or [])
        grounding_chunks = [
            {"url": chunk.web.uri, "title": chunk.web.title}
            for chunk in metadata.grounding_chunks or []
            if chunk.web
        ]
        grounding_supports = [
            {
                "segment_text": support.segment.text,
                "start_index": support.segment.start_index,
                "end_index": support.segment.end_index,
                "source_indices": list(support.grounding_chunk_indices),
            }
            for support in metadata.grounding_supports or []
        ]
        if (
            metadata.search_entry_point
            and metadata.search_entry_point.rendered_content
        ):
            search_widget_html = metadata.search_entry_point.rendered_content
    except Exception:
        pass

    return web_search_queries, grounding_chunks, grounding_supports, search_widget_html


def _process_grounding_response(
    response: Any,
    model_id: str,
    query: str,
    structured_output: bool,
    fallback_chain: list[str],
    fallback_reason: str | None,
    span: Any,
) -> GeminiGroundingResult:
    """Process a successful Gemini grounding response into a result."""
    answer_parts: list[str] = []
    thought_parts: list[str] = []

    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.thought:
                thought_parts.append(part.text or "")
            else:
                answer_parts.append(part.text or "")

    answer = "\n".join(answer_parts)
    thoughts = "\n".join(thought_parts) if thought_parts else None

    structured_result = None
    if structured_output:
        try:
            parsed_response = getattr(response, "parsed", None)
            if parsed_response:
                structured_result = parsed_response.model_dump()
            elif answer:
                parsed = GeminiResearchOutput.model_validate_json(answer)
                structured_result = parsed.model_dump()
        except Exception as exc:
            logger.warning(
                "Structured Gemini grounding output failed to parse: %s", exc
            )

    url_citations = _extract_url_citations(response)
    web_search_queries, grounding_chunks, grounding_supports, search_widget_html = (
        _extract_grounding_metadata(response)
    )
    usage = extract_llm_usage(response)

    span.set_attribute("llm.model_name", model_id)
    span.set_attribute("search.model_used", model_id)
    if usage:
        if usage.input_tokens is not None:
            span.set_attribute("llm.token_count.prompt", usage.input_tokens)
        if usage.output_tokens is not None:
            span.set_attribute("llm.token_count.completion", usage.output_tokens)
        if usage.total_tokens is not None:
            span.set_attribute("llm.token_count.total", usage.total_tokens)
    span.set_attribute("search.web_search_query_count", len(web_search_queries))
    span.set_attribute("search.grounding_chunk_count", len(grounding_chunks))
    span.set_attribute("search.grounding_support_count", len(grounding_supports))
    span.set_attribute("search.url_citation_count", len(url_citations))
    if fallback_chain:
        span.set_attribute("search.fallback_chain", ",".join(fallback_chain))
    set_span_success(span, result_count=len(grounding_chunks))

    return GeminiGroundingResult(
        query=query,
        answer=answer,
        thoughts=thoughts,
        structured_result=structured_result,
        model_used=model_id,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        structured_output=structured_output,
        web_search_queries=web_search_queries,
        grounding_chunks=grounding_chunks,
        grounding_supports=grounding_supports,
        url_citations=url_citations,
        search_widget_html=search_widget_html,
        fallback_chain=fallback_chain,
        fallback_reason=fallback_reason,
    )


async def _call_single_grounding(
    query: str,
    system_prompt: str,
    structured_output: bool,
    span_name: str = "grounded_search",
) -> GeminiGroundingResult:
    """Execute a single grounded search through the fallback tier.

    Args:
        query: The user query text.
        system_prompt: The full system instruction.
        structured_output: Whether to request structured JSON output.
        span_name: Telemetry span name.

    Returns:
        GeminiGroundingResult with answer, metadata, and grounding information.
    """
    types = _get_genai_types()

    fallback_chain: list[str] = []
    fallback_reason: str | None = None

    with create_llm_operation_span(
        span_name,
        system="google",
        attributes={
            "llm.model_name": GEMINI_GROUNDING_TIER[0],
            "search.query": query[:500],
            "search.structured_output": structured_output,
            "search.fallback_tier_count": len(GEMINI_GROUNDING_TIER),
        },
    ) as span:
        async def _try_model(
            model_id: str,
            client: Any,
            config: Any,
            contents: str,
        ) -> GeminiGroundingResult:
            """Try one model. Returns result or raises to trigger fallback."""
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=contents,
                config=config,
            )
            return _process_grounding_response(
                response, model_id, query, structured_output,
                fallback_chain, fallback_reason, span,
            )

        for model_id in GEMINI_GROUNDING_TIER:
            api_key = _api_key_for_model(model_id)
            client = get_gemini_client(api_key)
            if not client:
                fallback_reason = (
                    f"Set {'GEMINI_SECOND_API_KEY' if model_id == GEMINI_GROUNDING_TIER[-1] else 'GEMINI_API_KEY'} "
                    f"environment variable for {model_id}"
                )
                logger.warning("Skipping Gemini grounding tier %s: %s", model_id, fallback_reason)
                continue

            fallback_chain.append(model_id)

            config_dict = _build_grounding_config(
                structured_output=structured_output, model_id=model_id
            )

            is_gemini = _is_gemini_model(model_id)
            is_gemini3 = _is_gemini3_model(model_id)

            if is_gemini:
                config_dict["system_instruction"] = system_prompt

            # For 2.5 models: structured output via json_schema is unsupported
            # with grounding. Use text-append approach instead.
            use_query = query
            if structured_output and not is_gemini3:
                use_query = (
                    f"{query}\n\n"
                    f"Respond in valid JSON with this exact structure (no markdown fences):\n"
                    f'{{"executive_summary": "brief summary", '
                    f'"key_findings": ["finding with [N] citation", ...], '
                    f'"sources": [{{"url": "https://...", "title": "Source Title"}}], '
                    f'"confidence": "high|medium|low", '
                    f'"uncertainties": null or ["gap description"]}}'
                )
                # Don't set response_mime_type on 2.5 models
                config_dict.pop("response_mime_type", None)
                config_dict.pop("response_json_schema", None)

            config = types.GenerateContentConfig(**config_dict)

            try:
                return await _try_model(model_id, client, config, use_query)
            except Exception as exc:
                error_type, should_fallback, should_retry = _classify_gemini_error(exc)

                logger.warning(
                    "Gemini grounding attempt failed for %s: %s (type=%s, fallback=%s, retry=%s)",
                    model_id, exc, error_type, should_fallback, should_retry,
                )

                fallback_reason = f"{error_type}: {str(exc)}"

                if should_retry:
                    logger.info(
                        "Rate limit hit for %s, retrying once with 1s backoff", model_id
                    )
                    await asyncio.sleep(1)
                    try:
                        return await _try_model(model_id, client, config, use_query)
                    except Exception as retry_exc:
                        logger.warning("Retry also failed for %s: %s", model_id, retry_exc)

                continue

        # All tiers exhausted
        logger.error("All Gemini grounding tiers exhausted for query: %s", query)
        final_error = fallback_reason or "All tiers exhausted"
        set_span_error(span, RuntimeError(final_error))
        return GeminiGroundingResult(
            query=query,
            answer="",
            model_used=GEMINI_GROUNDING_TIER[-1],
            structured_output=structured_output,
            fallback_chain=fallback_chain,
            fallback_reason=final_error,
            error=f"All fallback models failed. Tried: {', '.join(fallback_chain)}",
        )


async def gemini_search_with_grounding(
    query: str,
    structured_output: bool = False,
    research_goal: str | None = None,
) -> GeminiGroundingResult:
    """Execute Gemini grounding with fallback tier and optional structured output.

    Args:
        query: The research query to search.
        structured_output: If True, request structured JSON output.
        research_goal: Optional context/goal from client to guide research focus.

    Returns:
        GeminiGroundingResult with answer, metadata, and grounding information.
    """
    formatted_system_prompt = get_system_prompt(research_goal)
    return await _call_single_grounding(
        query=query,
        system_prompt=formatted_system_prompt,
        structured_output=structured_output,
    )


async def gemini_search_with_grounding_dual(
    query: str,
    structured_output: bool = False,
    research_goal: str | None = None,
) -> dict[str, Any]:
    """Execute two parallel grounded searches — overview + deepdive — via asyncio.gather.

    Overview branch: broad coverage, multiple perspectives, landscape synthesis.
    Deepdive branch: precise fact extraction, exact numbers, primary sources.

    Args:
        query: The research query to search.
        structured_output: If True, request structured JSON output.
        research_goal: Optional context/goal from client.

    Returns:
        dict with keys: overview (GeminiGroundingResult), deepdive (GeminiGroundingResult),
        both_succeeded (bool), model_used (str).
    """
    goal = research_goal or query
    sys_ov, user_ov = build_dual_prompt(query=query, research_goal=goal, mode="overview")
    sys_dd, user_dd = build_dual_prompt(query=query, research_goal=goal, mode="deepdive")

    with create_llm_operation_span(
        "grounded_search_dual",
        system="google",
        attributes={
            "search.query": query[:500],
            "search.structured_output": structured_output,
            "search.research_goal": goal[:500],
                "search.dual_mode": True,
            "llm.model_name": GEMINI_GROUNDING_TIER[0],
        },
    ) as span:
        result_ov, result_dd = await asyncio.gather(
            _call_single_grounding(
                query=user_ov,
                system_prompt=sys_ov,
                structured_output=structured_output,
                span_name="grounded_search_overview",
            ),
            _call_single_grounding(
                query=user_dd,
                system_prompt=sys_dd,
                structured_output=structured_output,
                span_name="grounded_search_deepdive",
            ),
        )

        both_succeeded = bool(
            result_ov.answer and result_dd.answer
            and not result_ov.error and not result_dd.error
        )

        span.set_attribute("search.overview_succeeded", bool(result_ov.answer))
        span.set_attribute("search.deepdive_succeeded", bool(result_dd.answer))
        span.set_attribute("search.both_succeeded", both_succeeded)
        set_span_success(span, result_count=2 if both_succeeded else 1)

        return {
            "overview": result_ov.model_dump(exclude_none=True),
            "deepdive": result_dd.model_dump(exclude_none=True),
            "both_succeeded": both_succeeded,
            "model_used": result_ov.model_used or result_dd.model_used,
        }
