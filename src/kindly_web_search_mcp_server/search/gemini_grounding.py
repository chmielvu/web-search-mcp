"""Gemini Google Search grounding helper."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from ..settings import settings

logger = logging.getLogger(__name__)
_gemini_client: genai.Client | None = None

RESEARCH_SYSTEM_PROMPT = """
<role>You are a research analyst with access to Google Search grounding.</role>

<constraints>
1. Be objective and factual.
2. Cite all sources inline using [N] notation.
3. Mark uncertainty clearly.
4. No speculation without sources.
</constraints>

<task>
Given a query:
1. Plan research strategy
2. Execute searches via Google Search grounding
3. Cross-reference across sources
4. Synthesize into structured report

<research_goal>
{research_goal}
</research_goal>

Keep the research goal in mind when prioritizing which information to surface.

Output:
- Executive summary first
- Key findings with inline [N] citations
- Sources section at end
- Mark report after "---" line
</task>
"""


class GeminiResearchOutput(BaseModel):
    """Structured research output schema for Gemini grounding."""

    executive_summary: str = Field(description="Brief 1-2 sentence summary")
    key_findings: list[str] = Field(description="Main findings with citations")
    sources: list[dict[str, str]] = Field(description="Source URLs and titles")
    confidence: str = Field(description="high/medium/low")
    uncertainties: list[str] | None = Field(default=None)


class GeminiGroundingResult(BaseModel):
    """Result from Gemini Google Search grounding."""

    query: str = Field(description="Original search query")
    answer: str = Field(description="Generated answer text")
    thoughts: str | None = Field(default=None, description="Internal reasoning if available")
    structured_result: dict[str, Any] | None = Field(default=None, description="Parsed structured output")
    model_used: str = Field(description="Model ID used for generation")
    structured_output: bool = Field(description="Whether structured output was requested")
    web_search_queries: list[str] = Field(default_factory=list, description="Queries sent to Google Search")
    grounding_chunks: list[dict[str, str]] = Field(
        default_factory=list, description="Source chunks with URL and title"
    )
    grounding_supports: list[dict[str, Any]] = Field(
        default_factory=list, description="Segment-to-source mappings"
    )
    search_widget_html: str | None = Field(default=None, description="Search widget HTML for display")
    error: str | None = Field(default=None, description="Error message if failed")


def get_gemini_client() -> genai.Client | None:
    """Lazy-init Gemini client."""
    global _gemini_client
    if _gemini_client is None and settings.gemini_api_key:
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


async def gemini_search_with_grounding(
    query: str,
    structured_output: bool = False,
    research_goal: str | None = None,
) -> GeminiGroundingResult:
    """Execute Gemini grounding with optional structured output.

    Args:
        query: The research query to search
        structured_output: If True, request structured JSON output
        research_goal: Optional context/goal from client to guide research focus

    Returns:
        GeminiGroundingResult with answer, metadata, and grounding information
    """
    client = get_gemini_client()
    if not client:
        return GeminiGroundingResult(
            query=query,
            answer="",
            model_used=settings.gemini_grounding_model,
            structured_output=structured_output,
            error="Set KINDLY_GEMINI_API_KEY environment variable",
        )

    # Format system prompt with research goal if provided
    goal = research_goal or "Gather relevant information for the query"
    formatted_system_prompt = RESEARCH_SYSTEM_PROMPT.format(research_goal=goal)

    config_dict: dict[str, Any] = {
        "system_instruction": formatted_system_prompt,
        "tools": [types.Tool(google_search=types.GoogleSearch())],
    }

    if structured_output:
        config_dict["response_mime_type"] = "application/json"
        config_dict["response_json_schema"] = GeminiResearchOutput.model_json_schema()

    model_id = settings.gemini_grounding_model

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model_id,
            contents=query,
            config=types.GenerateContentConfig(**config_dict),
        )

        # Separate thought parts from answer parts
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

        # Parse structured output if requested
        structured_result = None
        if structured_output and answer:
            try:
                parsed = GeminiResearchOutput.model_validate_json(answer)
                structured_result = parsed.model_dump()
            except Exception as exc:
                logger.warning("Structured Gemini grounding output failed to parse: %s", exc)

        # Extract grounding metadata
        web_search_queries: list[str] = []
        grounding_chunks: list[dict[str, str]] = []
        grounding_supports: list[dict[str, Any]] = []
        search_widget_html: str | None = None

        if response.candidates and response.candidates[0].grounding_metadata:
            metadata = response.candidates[0].grounding_metadata
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
            if metadata.search_entry_point and metadata.search_entry_point.rendered_content:
                search_widget_html = metadata.search_entry_point.rendered_content

        return GeminiGroundingResult(
            query=query,
            answer=answer,
            thoughts=thoughts,
            structured_result=structured_result,
            model_used=model_id,
            structured_output=structured_output,
            web_search_queries=web_search_queries,
            grounding_chunks=grounding_chunks,
            grounding_supports=grounding_supports,
            search_widget_html=search_widget_html,
        )

    except Exception as exc:
        logger.error("Gemini grounding failed: %s", exc)
        return GeminiGroundingResult(
            query=query,
            answer="",
            model_used=settings.gemini_grounding_model,
            structured_output=structured_output,
            error=str(exc),
        )