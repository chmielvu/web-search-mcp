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
import os
from typing import Any

from pydantic import BaseModel, Field

from ..inference.adapters.genai import get_genai_client as _adapter_get_genai_client
from ..prompts.provider_gemini import build_dual_prompt, build_provider_gemini_prompt
from ..settings import settings
from ..telemetry import create_llm_operation_span, set_span_error, set_span_success

logger = logging.getLogger(__name__)
_genai_module: Any | None = None
_genai_types: Any | None = None

GEMINI_GROUNDING_TIER = [
    "gemini-3.1-flash-lite",  # PRIMARY — Gemini 3.x grounding + structured output
    "gemini-2.5-flash",  # FAST FALLBACK — best 2.x grounding quality
    "gemini-2.5-flash-lite",  # LAST-RESORT FALLBACK
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

    executive_summary: str = Field(description="High-level answer summary")
    key_findings: list[str] = Field(
        description="Key findings with [N] inline citations matching source index"
    )
    sources: list[GeminiSource] = Field(description="Referenced sources list")
    confidence: str = Field(default="medium", description="Confidence level: high, medium, low")
    uncertainties: list[str] | None = Field(default=None)


class GeminiGroundingResult(BaseModel):
    """Output contract for gemini_search tool response."""

    query: str
    mode: str = "single"  # "single" or "dual"
    answer: str = ""
    structured_data: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    model_used: str = GEMINI_GROUNDING_TIER[0]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    grounding_chunks_count: int = 0
    web_search_queries_count: int = 0
    url_citations: list[dict[str, Any]] = Field(default_factory=list)
    search_widget_html: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    error: str | None = None

    @property
    def input_tokens(self) -> int:
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        return self.completion_tokens

    @property
    def grounding_chunks(self) -> list[dict[str, Any]]:
        return self.sources

    @property
    def structured_output(self) -> bool:
        return self.structured_data is not None

    @property
    def structured_result(self) -> dict[str, Any] | None:
        return self.structured_data


def _get_genai_module() -> tuple[Any, Any]:
    global _genai_module, _genai_types
    if _genai_module is None:
        from google import genai
        from google.genai import types

        _genai_module = genai
        _genai_types = types
    return _genai_module, _genai_types


def _get_genai_types() -> Any:
    return _get_genai_module()[1]


def get_gemini_client(api_key: str | None = None) -> Any:
    """Get or create Google GenAI SDK client."""
    return _adapter_get_genai_client(api_key)


def _api_key_for_model(model_id: str) -> str:
    if model_id == GEMINI_GROUNDING_TIER[-1]:
        return (
            getattr(settings, "gemini_second_api_key", "")
            or os.environ.get("GEMINI_SECOND_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or getattr(settings, "gemini_api_key", "")
        )
    return getattr(settings, "gemini_api_key", "") or os.environ.get("GEMINI_API_KEY") or ""


def _is_gemini3_model(model_id: str) -> bool:
    return "3." in model_id or "gemini-3" in model_id


def _is_gemini_model(model_id: str) -> bool:
    return "gemini" in model_id.lower()


def _build_grounding_config(structured_output: bool, model_id: str) -> dict[str, Any]:
    types = _get_genai_types()

    config_dict: dict[str, Any] = {
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": 1.0,
    }

    if _is_gemini3_model(model_id):
        config_dict["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

    if structured_output:
        if _is_gemini3_model(model_id):
            config_dict["response_mime_type"] = "application/json"
            config_dict["response_schema"] = GeminiResearchOutput

    return config_dict


def _classify_gemini_error(exc: Exception) -> tuple[str, bool, bool]:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limit", True, True
    if status_code == 404:
        return "model_not_found", True, False
    if status_code in (500, 503):
        return "service_unavailable", True, False

    err_str = str(exc).lower()
    if "429" in err_str or "resourceexhausted" in err_str or "quota" in err_str:
        return "rate_limit", True, True
    if "404" in err_str or "not_found" in err_str:
        return "model_not_found", True, False
    if "500" in err_str or "503" in err_str or "server" in err_str or "internal" in err_str:
        return "service_unavailable", True, False

    return "unknown", True, False


def _extract_url_citations(response: Any) -> list[dict[str, Any]]:
    citations = []
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                annotations = (
                    getattr(part, "annotations", None) or getattr(part, "url_citations", None) or []
                )
                for ann in annotations:
                    url = getattr(ann, "url", None) or (
                        ann.get("url") if isinstance(ann, dict) else None
                    )
                    title = getattr(ann, "title", None) or (
                        ann.get("title") if isinstance(ann, dict) else None
                    )
                    if url:
                        citations.append({"url": url, "title": title})
    except Exception as exc:
        logger.debug("Failed to extract url_citations: %s", exc)
    return citations


def _process_grounding_response(
    response: Any,
    model_id: str,
    query: str,
    structured_output: bool,
    fallback_chain: list[str],
    fallback_reason: str | None,
    span: Any = None,
) -> GeminiGroundingResult:
    raw_text = getattr(response, "text", None)
    if not raw_text and hasattr(response, "candidates") and response.candidates:
        cand = response.candidates[0]
        content = getattr(cand, "content", None)
        if content and hasattr(content, "parts") and content.parts:
            raw_text = getattr(content.parts[0], "text", "") or ""
    raw_text = raw_text or ""

    structured_data: dict[str, Any] | None = None
    if structured_output and raw_text:
        import json

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].startswith("```") else lines
            cleaned = "\n".join(lines).strip()
        try:
            structured_data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse structured output on %s: %s", model_id, exc)

    sources: list[dict[str, Any]] = []
    grounding_chunks_count = 0
    web_search_queries: list[str] = []
    search_widget_html: str | None = None

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        candidate = candidates[0]
        grounding_metadata = getattr(candidate, "grounding_metadata", None)
        if grounding_metadata:
            grounding_chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
            grounding_chunks_count = len(grounding_chunks)
            for chunk in grounding_chunks:
                web = getattr(chunk, "web", None)
                if web:
                    sources.append(
                        {
                            "url": getattr(web, "uri", "") or "",
                            "title": getattr(web, "title", "") or "",
                        }
                    )

            queries = getattr(grounding_metadata, "web_search_queries", None) or []
            web_search_queries = [str(q) for q in queries]
            entry_point = getattr(grounding_metadata, "search_entry_point", None)
            if entry_point and hasattr(entry_point, "rendered_content"):
                search_widget_html = getattr(entry_point, "rendered_content", None)

    url_citations = _extract_url_citations(response)

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    completion_tokens = (
        getattr(usage, "response_token_count", 0)
        or getattr(usage, "candidates_token_count", 0)
        or 0
    )
    total_tokens = getattr(usage, "total_token_count", 0) or 0

    if span:
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
        span.set_attribute("llm.token_count.completion", completion_tokens)
        span.set_attribute("llm.token_count.total", total_tokens)
        span.set_attribute("grounding.chunks_count", grounding_chunks_count)
        span.set_attribute("grounding.web_search_queries_count", len(web_search_queries))
        if fallback_chain:
            span.set_attribute("search.fallback_chain", ",".join(fallback_chain))

    answer_text = raw_text
    if structured_data and "executive_summary" in structured_data:
        summary = structured_data["executive_summary"]
        findings = structured_data.get("key_findings") or []
        findings_text = "\n".join(f"- {f}" for f in findings)
        answer_text = f"{summary}\n\nKey Findings:\n{findings_text}"

    return GeminiGroundingResult(
        query=query,
        mode="single",
        answer=answer_text,
        structured_data=structured_data,
        sources=sources,
        search_queries=web_search_queries,
        model_used=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        grounding_chunks_count=grounding_chunks_count,
        web_search_queries_count=len(web_search_queries),
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
    """Execute a single grounded search through the fallback tier."""
    with create_llm_operation_span(
        span_name,
        system="google",
        attributes={
            "llm.model_name": GEMINI_GROUNDING_TIER[0],
            "search.query": query[:500],
            "search.fallback_tier_count": len(GEMINI_GROUNDING_TIER),
            "search.structured_output": structured_output,
        },
    ) as span:
        types = _get_genai_types()

        fallback_chain: list[str] = []
        fallback_reason: str | None = None
        last_error: Exception | None = None

        for model_id in GEMINI_GROUNDING_TIER:
            config_dict = _build_grounding_config(
                structured_output=structured_output, model_id=model_id
            )

            is_gemini = _is_gemini_model(model_id)
            is_gemini3 = _is_gemini3_model(model_id)

            if is_gemini:
                config_dict["system_instruction"] = system_prompt

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
                config_dict.pop("response_mime_type", None)
                config_dict.pop("response_json_schema", None)

            config = types.GenerateContentConfig(**config_dict)

            for attempt in range(2):
                api_key = _api_key_for_model(model_id)
                client = get_gemini_client(api_key)
                if not client:
                    fallback_reason = (
                        f"Set {'GEMINI_SECOND_API_KEY' if model_id == GEMINI_GROUNDING_TIER[-1] else 'GEMINI_API_KEY'} "
                        f"environment variable for {model_id}"
                    )
                    logger.warning(
                        "Skipping Gemini grounding tier %s: %s", model_id, fallback_reason
                    )
                    break

                if model_id not in fallback_chain:
                    fallback_chain.append(model_id)

                try:
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model_id,
                        contents=use_query,
                        config=config,
                    )
                    result = _process_grounding_response(
                        response,
                        model_id,
                        query,
                        structured_output,
                        fallback_chain,
                        fallback_reason,
                        span=span,
                    )
                    span.set_attribute(
                        "search.grounding_chunk_count", result.grounding_chunks_count
                    )
                    span.set_attribute("search.model_used", result.model_used)
                    span.set_attribute("llm.model_name", result.model_used)
                    span.set_attribute(
                        "search.web_search_queries_count", result.web_search_queries_count
                    )
                    set_span_success(span, result_count=len(result.sources))
                    return result
                except Exception as exc:
                    error_type, should_fallback, should_retry = _classify_gemini_error(exc)
                    fallback_reason = error_type
                    last_error = exc
                    logger.warning(
                        "Gemini grounding attempt failed for %s (attempt %d): %s",
                        model_id,
                        attempt + 1,
                        exc,
                    )
                    if should_retry and attempt == 0:
                        continue
                    break

        err_msg = (
            f"All fallback models failed: {last_error}"
            if last_error
            else "All fallback models failed"
        )
        set_span_error(span, last_error or RuntimeError(err_msg))
        return GeminiGroundingResult(
            query=query,
            mode="single",
            answer="",
            model_used=fallback_chain[-1] if fallback_chain else GEMINI_GROUNDING_TIER[0],
            fallback_chain=fallback_chain,
            fallback_reason=fallback_reason,
            error=err_msg,
        )


async def gemini_search_with_grounding(
    query: str,
    research_goal: str | None = None,
    structured_output: bool = True,
    parallel_mode: bool = True,
) -> GeminiGroundingResult:
    """Execute Gemini Grounding Search tool operation."""
    if getattr(settings, "gemini_search_backend", "grounding") == "antigravity":
        from .antigravity_backend import call_antigravity_grounding

        try:
            return await call_antigravity_grounding(
                query,
                system_prompt=get_system_prompt(research_goal),
                structured_output=structured_output,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to grounding tier
            logger.warning(
                "Antigravity backend failed (%s); falling back to grounding tier",
                exc,
            )
    if parallel_mode and structured_output:
        overview_system, _ = build_dual_prompt(
            query=query, research_goal=research_goal, mode="overview"
        )
        deepdive_system, _ = build_dual_prompt(
            query=query, research_goal=research_goal, mode="deepdive"
        )

        try:
            overview_task = _call_single_grounding(
                query,
                overview_system,
                structured_output=True,
                span_name="grounded_search.overview",
            )
            deepdive_task = _call_single_grounding(
                query,
                deepdive_system,
                structured_output=True,
                span_name="grounded_search.deepdive",
            )
            overview_res, deepdive_res = await asyncio.gather(overview_task, deepdive_task)

            merged_sources = list(overview_res.sources)
            existing_urls = {s.get("url") for s in merged_sources if s.get("url")}
            for src in deepdive_res.sources:
                if src.get("url") and src["url"] not in existing_urls:
                    merged_sources.append(src)
                    existing_urls.add(src["url"])

            merged_citations = list(overview_res.url_citations)
            existing_cit_urls = {c.get("url") for c in merged_citations if c.get("url")}
            for cit in deepdive_res.url_citations:
                if cit.get("url") and cit["url"] not in existing_cit_urls:
                    merged_citations.append(cit)
                    existing_cit_urls.add(cit["url"])

            merged_structured: dict[str, Any] = {}
            if overview_res.structured_data:
                merged_structured.update(overview_res.structured_data)
            if deepdive_res.structured_data:
                overview_findings = merged_structured.get("key_findings") or []
                deep_findings = deepdive_res.structured_data.get("key_findings") or []
                merged_structured["key_findings"] = list(
                    dict.fromkeys(overview_findings + deep_findings)
                )

            summary = merged_structured.get("executive_summary", overview_res.answer)
            findings = merged_structured.get("key_findings") or []
            findings_text = "\n".join(f"- {f}" for f in findings)
            combined_answer = f"{summary}\n\nKey Findings:\n{findings_text}"

            return GeminiGroundingResult(
                query=query,
                mode="dual",
                answer=combined_answer,
                structured_data=merged_structured if merged_structured else None,
                sources=merged_sources,
                search_queries=list(set(overview_res.search_queries + deepdive_res.search_queries)),
                model_used=overview_res.model_used,
                prompt_tokens=overview_res.prompt_tokens + deepdive_res.prompt_tokens,
                completion_tokens=overview_res.completion_tokens + deepdive_res.completion_tokens,
                total_tokens=overview_res.total_tokens + deepdive_res.total_tokens,
                grounding_chunks_count=overview_res.grounding_chunks_count
                + deepdive_res.grounding_chunks_count,
                web_search_queries_count=overview_res.web_search_queries_count
                + deepdive_res.web_search_queries_count,
                url_citations=merged_citations,
                search_widget_html=overview_res.search_widget_html
                or deepdive_res.search_widget_html,
                fallback_chain=overview_res.fallback_chain,
                fallback_reason=overview_res.fallback_reason,
            )
        except Exception as exc:
            logger.warning(
                "Dual-prompt parallel grounding search failed: %s, falling back to single prompt",
                exc,
            )

    system_prompt = get_system_prompt(research_goal)
    return await _call_single_grounding(
        query,
        system_prompt,
        structured_output=structured_output,
        span_name="grounded_search.single",
    )
