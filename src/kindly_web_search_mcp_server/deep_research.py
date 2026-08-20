"""Standalone deep_research MCP tool backed by the self-hosted node-DeepResearch engine.

SEP-1686 background-capable: registered with ``task=TaskConfig(mode="optional")``
so task-capable clients run it as a background task (poll for results) while
legacy clients run it synchronously. Mirrors the OMP ``vercel-deep-research``
extension contract (presets, depth aliases, SSE stream parsing).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastmcp.dependencies import CurrentContext
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from .settings import settings
from .tools._helpers import _record_tool_failure, _record_tool_success
from .tools.catalog import tool_kwargs
from .utils.http_client import get_http_client
from .utils.observability import emit_tool_observability_event

LOGGER = logging.getLogger(__name__)

# ── Preset configuration matrix (mirrors the OMP extension) ────────────────

RESEARCH_PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "team_size": 1,
        "token_budget": 50_000,
        "max_bad_attempts": 1,
        "max_returned_urls": 25,
    },
    "standard": {
        "team_size": 2,
        "token_budget": 300_000,
        "max_bad_attempts": 2,
        "max_returned_urls": 50,
    },
    "deep": {
        "team_size": 3,
        "token_budget": 1_000_000,
        "max_bad_attempts": 3,
        "max_returned_urls": 100,
    },
}

# Alias mapping for synonym repair (mirrors the OMP extension)
DEPTH_ALIASES: dict[str, str] = {
    # Quick synonyms
    "fast": "quick",
    "light": "quick",
    "shallow": "quick",
    "brief": "quick",
    "express": "quick",
    "low": "quick",
    # Standard synonyms
    "normal": "standard",
    "medium": "standard",
    "default": "standard",
    "balanced": "standard",
    # Deep synonyms
    "full": "deep",
    "thorough": "deep",
    "extensive": "deep",
    "comprehensive": "deep",
    "detailed": "deep",
    "heavy": "deep",
    "high": "deep",
    "max": "deep",
}


def _resolve_preset_key(depth: str | None) -> str:
    """Normalize an input depth string or synonym alias into a preset key."""
    if not depth:
        return "standard"
    normalized = depth.strip().lower()
    if normalized in RESEARCH_PRESETS:
        return normalized
    return DEPTH_ALIASES.get(normalized, "standard")


# ── Response models ────────────────────────────────────────────────────────


class DeepResearchReference(BaseModel):
    """Single cited source from the research engine."""

    url: str
    title: str | None = None
    snippet: str | None = None


class DeepResearchUsage(BaseModel):
    """Token usage reported by the research engine."""

    total_tokens: int | None = None


class DeepResearchResponse(BaseModel):
    """Final deep_research tool response."""

    query: str
    preset: str
    team_size: int
    token_budget: int
    answer: str
    references: list[DeepResearchReference] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    read_urls: list[str] = Field(default_factory=list)
    all_urls: list[str] = Field(default_factory=list)
    usage: DeepResearchUsage | None = None
    report_markdown: str = ""


# ── SSE stream consumption ─────────────────────────────────────────────────


async def _consume_sse_stream(
    response: Any,
    *,
    preset_key: str,
    ctx: Context,
) -> dict[str, Any]:
    """Consume the engine's SSE stream, reporting progress, returning the final payload.

    Raises:
        ToolError: On an ``error`` event or if the stream closes without a
            final ``result`` payload.
    """
    final_result: dict[str, Any] | None = None
    event_name = "message"
    data_lines: list[str] = []
    action_count = 0

    async def _flush() -> None:
        nonlocal final_result, action_count
        if not data_lines:
            return
        data_string = "".join(data_lines)
        data_lines.clear()
        try:
            payload = json.loads(data_string)
        except json.JSONDecodeError:
            return

        if event_name == "action" and isinstance(payload, dict):
            action_count += 1
            step_type = payload.get("type")
            if step_type == "search":
                query = payload.get("query") or ", ".join(payload.get("queries") or [])
                summary = f"Searching: {query}"
            elif step_type == "visit":
                summary = f"Visiting: {payload.get('url')}"
            elif step_type == "reflect":
                summary = "Evaluating findings & planning next step..."
            elif step_type == "coding":
                summary = "Executing sandbox validation..."
            elif step_type:
                summary = f"Action: {step_type}"
            else:
                summary = "Executing research step..."
            await ctx.report_progress(
                progress=min(action_count, 100),
                total=100,
                message=f"[{preset_key.upper()}] {summary}",
            )
            await ctx.info(f"[{preset_key.upper()}] {summary}")
        elif event_name == "result" and isinstance(payload, dict):
            final_result = payload
        elif event_name == "error" and isinstance(payload, dict):
            raise ToolError(payload.get("error") or "Server returned research error event")

    async for line in response.aiter_lines():
        if line == "":
            await _flush()
            event_name = "message"
            continue
        if line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_lines.append(line[6:])
    await _flush()

    if final_result is None:
        raise ToolError("Stream closed before receiving final 'result' payload.")
    return final_result


def _build_report_markdown(
    *,
    query: str,
    preset_key: str,
    team_size: int,
    token_budget: int,
    final: dict[str, Any],
) -> str:
    """Render the human-readable markdown report (mirrors the OMP extension)."""
    references = final.get("references") or []
    visited = final.get("visitedURLs") or []
    all_urls = final.get("allURLs") or []
    usage = final.get("usage") or {}
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None

    lines: list[str] = [
        f"# Deep Research Report: {query}",
        f"*Preset:* `{preset_key.upper()}` | *Workers:* `{team_size}` | "
        f"*Token Budget:* `{token_budget:,}`",
        "",
        final.get("answer") or "No synthesis answer generated.",
        "",
        "---",
        "### 📚 Primary References & Sources",
    ]
    for idx, ref in enumerate(references, start=1):
        if isinstance(ref, dict):
            title = ref.get("title") or ref.get("url") or "Unknown"
            url = ref.get("url") or ""
            snippet = ref.get("snippet") or "No snippet available"
            lines.append(f"[{idx}] [{title}]({url}) — *{snippet}*")
    lines += [
        "",
        "### 📊 Execution Statistics",
        f"- **URLs Visited:** {len(visited)}",
        f"- **URLs Analyzed:** {len(all_urls)}",
        f"- **Total Tokens Used:** {total_tokens if total_tokens is not None else 'N/A'}",
    ]
    return "\n".join(lines)


# ── Tool implementation ────────────────────────────────────────────────────


async def deep_research(
    query: str = "",
    question: str | None = None,
    topic: str | None = None,
    prompt: str | None = None,
    depth: str = "standard",
    with_images: bool = False,
    language_code: str | None = None,
    token_budget_override: Annotated[
        int | None, Field(ge=1000, description="Override preset token budget (min 1,000).")
    ] = None,
    team_size_override: Annotated[
        int | None, Field(ge=1, le=5, description="Override preset worker count (1-5).")
    ] = None,
    endpoint_override: Annotated[
        str | None,
        Field(description="Override target self-hosted Deep Research deployment URL."),
    ] = None,
    ctx: Context = CurrentContext(),
) -> DeepResearchResponse:
    """Autonomous multi-step web research via the self-hosted node-DeepResearch engine.

    PRESET DEPTHS:
    - 'quick' (or 'fast', 'brief'): 1 worker, 50k token budget (~15s). Fast scoping or basic validation.
    - 'standard' (DEFAULT): 2 workers, 300k token budget (~45s). Balanced multi-source technical research.
    - 'deep' (or 'thorough', 'full'): 3 workers, 1M token budget (~120s+). Exhaustive multi-site investigation.

    WHEN TO USE:
    - Multi-source technical investigations, SDK/library comparisons, or architectural trade-off analysis.
    - Finding obscure bug fixes across developer documentation and forums.

    WHEN NOT TO USE:
    - Local codebase searches (use code_search).
    - Simple single-fact questions that basic web_search can answer.

    This tool is background-capable: task-aware clients run it as a background
    task and poll for progress; other clients run it synchronously.

    Args:
        query: The research topic or explicit technical question to investigate.
        question: Alias for query.
        topic: Alias for query.
        prompt: Alias for query.
        depth: Research intensity preset: 'quick' (or 'fast'), 'standard', or 'deep' (or 'full'/'thorough'). Default: 'standard'.
        with_images: Enable multimodal image/visual analysis during web page visits. Default: False.
        language_code: ISO language code for output localization (e.g. 'en', 'es', 'zh').
        token_budget_override: Override preset token budget (min 1,000).
        team_size_override: Override preset worker count (1-5).
        endpoint_override: Override target self-hosted Deep Research deployment URL.
    """
    started = time.monotonic()
    tool_call_id = str(uuid4())

    # 1. Repair & normalize query (fallback to alias keys, mirrors the OMP extension)
    raw_query = (query or question or topic or prompt or "").strip()
    if not raw_query:
        raise ToolError("Research query cannot be empty. Pass query, question, topic, or prompt.")

    # 2. Resolve preset key and final parameters
    preset_key = _resolve_preset_key(depth)
    preset = RESEARCH_PRESETS[preset_key]
    token_budget = token_budget_override or preset["token_budget"]
    team_size = team_size_override or preset["team_size"]
    target_endpoint = (endpoint_override or settings.deep_research_url).rstrip("/")
    stream_url = f"{target_endpoint}/research/stream"

    emit_tool_observability_event(
        LOGGER,
        "deep_research",
        "request",
        tool_call_id=tool_call_id,
        query=raw_query,
        preset=preset_key,
        team_size=team_size,
        token_budget=token_budget,
        with_images=with_images,
        language_code=language_code,
    )

    await ctx.report_progress(
        progress=0,
        total=100,
        message=f"Starting Deep Research [{preset_key.upper()}]...",
    )
    await ctx.info(
        f"Starting Deep Research [preset={preset_key.upper()}, workers={team_size}, "
        f"budget={token_budget:,}] for: {raw_query}"
    )

    headers = {"Content-Type": "application/json"}
    if settings.deep_research_secret:
        headers["Authorization"] = f"Bearer {settings.deep_research_secret}"

    payload = {
        "query": raw_query,
        "token_budget": token_budget,
        "team_size": team_size,
        "max_attempts": preset["max_bad_attempts"],
        "max_returned_urls": preset["max_returned_urls"],
        "with_images": with_images,
        "language_code": language_code,
    }

    try:
        client = await get_http_client()
        async with client.stream(
            "POST",
            stream_url,
            json=payload,
            headers=headers,
            timeout=settings.deep_research_timeout_seconds,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ToolError(f"Deep Research HTTP {response.status_code} error: {body[:500]}")
            final = await _consume_sse_stream(response, preset_key=preset_key, ctx=ctx)
    except ToolError:
        _record_tool_failure("deep_research")
        raise
    except httpx.TimeoutException as exc:
        _record_tool_failure("deep_research")
        raise ToolError(
            f"Deep Research timed out after {settings.deep_research_timeout_seconds:g}s. "
            "Try depth='quick' or check the endpoint."
        ) from exc
    except httpx.HTTPError as exc:
        _record_tool_failure("deep_research")
        raise ToolError(
            f"Deep Research connection error: {exc}. "
            f"Check endpoint status at {target_endpoint}/health or try depth='quick'."
        ) from exc

    # 3. Build the structured response + markdown report
    references = [
        DeepResearchReference(**ref)
        for ref in (final.get("references") or [])
        if isinstance(ref, dict)
    ]
    visited_urls = [str(u) for u in (final.get("visitedURLs") or [])]
    read_urls = [str(u) for u in (final.get("readURLs") or [])]
    all_urls = [str(u) for u in (final.get("allURLs") or [])]
    usage_raw = final.get("usage")
    usage = (
        DeepResearchUsage(total_tokens=usage_raw.get("total_tokens"))
        if isinstance(usage_raw, dict)
        else None
    )

    report_markdown = _build_report_markdown(
        query=raw_query,
        preset_key=preset_key,
        team_size=team_size,
        token_budget=token_budget,
        final=final,
    )

    await ctx.report_progress(progress=100, total=100, message="Deep Research complete")
    await ctx.info(
        f"Deep Research complete: {len(references)} references, {len(visited_urls)} URLs visited"
    )

    emit_tool_observability_event(
        LOGGER,
        "deep_research",
        "response",
        tool_call_id=tool_call_id,
        query=raw_query,
        preset=preset_key,
        references=len(references),
        visited_urls=len(visited_urls),
        total_tokens=usage.total_tokens if usage else None,
        duration_ms=(time.monotonic() - started) * 1000,
    )
    _record_tool_success(
        "deep_research",
        input_query=raw_query,
        output_result_count=len(references),
        output_content=report_markdown,
    )

    return DeepResearchResponse(
        query=raw_query,
        preset=preset_key,
        team_size=team_size,
        token_budget=token_budget,
        answer=final.get("answer") or "No synthesis answer generated.",
        references=references,
        visited_urls=visited_urls,
        read_urls=read_urls,
        all_urls=all_urls,
        usage=usage,
        report_markdown=report_markdown,
    )


# ── MCP Registration ───────────────────────────────────────────────────────


def register_deep_research(mcp: Any) -> None:
    """Register the deep_research tool on the given FastMCP server.

    ``task=TaskConfig(mode="optional")`` (emitted by the tool catalog) marks
    the tool as background-capable (SEP-1686): task-capable clients run it as
    a background task and poll for results; legacy clients run it synchronously.
    """
    mcp.tool(**tool_kwargs("deep_research"))(deep_research)


__all__ = [
    "DEPTH_ALIASES",
    "DeepResearchReference",
    "DeepResearchResponse",
    "DeepResearchUsage",
    "RESEARCH_PRESETS",
    "deep_research",
    "register_deep_research",
]
