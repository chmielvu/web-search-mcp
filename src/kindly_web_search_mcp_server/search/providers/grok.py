"""Grok 4.3 search via OpenRouter with native web_search + x_search.

OpenRouter's openrouter:web_search server tool with engine: "native" routes
through xAI's own search infrastructure. For xAI models, both web_search and
x_search fire when the prompt mentions X/Twitter content.

Architecture:
- Light provider: extracts structured WebSearchResult[] from url_citation
  annotations for RRF merge pipeline participation
- Standalone tool: returns synthesized answer + citation list (like
  gemini_search)

Prompts engineered from:
- Grok 4.3 beta system prompt leak (GitHub): XML structure, self-aware identity
- understandingai.net: explicit real-time data instructions, date anchoring
- Rephrase-it.com: XML tags for semantic sections, concision over verbosity
- Community consensus: XML > Markdown, be specific, cite sources
- Real API testing (2026-06-02): 5-line prompt outperformed verbose versions;
  x_search fires when prompt explicitly references X/Twitter
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ...models import WebSearchResult
from ...prompts.provider_grok import build_provider_grok_prompt
from ...telemetry.usage import extract_llm_usage
from ...settings import settings
from ...telemetry import create_llm_operation_span, set_span_error, set_span_success
from .base import run_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "x-ai/grok-4.3"
REQUEST_TIMEOUT = 60.0

# ---------------------------------------------------------------------------
# Prompts — engineered for Grok 4.3 tool-calling search
# ---------------------------------------------------------------------------

_GROK_SYSTEM_PROMPT = """\
<identity>
Research assistant. Web + X search enabled. Today: {today}.
</identity>
<instructions>
1. Search web AND X/Twitter for the most current information
2. Cite every claim with [source](url) links
3. If sources conflict, acknowledge this explicitly
4. Be concise and precise
</instructions>
<format>
Answer directly with inline citations. End with a Sources section listing
[title](url) links.
</format>"""

_GROK_USER_PROMPT = """\
<query>{query}</query>
<goal>{research_goal}</goal>
<requirements>
- Search web and X/Twitter for current information
- Cite sources with links
- Flag conflicting or uncertain information
- Prioritize the research goal
</requirements>"""

_PROVIDER_SYSTEM_PROMPT = """\
<identity>
Web search provider. Find and list relevant URLs. Today: {today}.
</identity>
<instructions>
1. Search web and X/Twitter for: {query}
2. Return exactly {num_results} results as:
#. TITLE — URL — one-paragraph-summary
3. Each result MUST start with "#. " followed by the title
4. Prefer official documentation over blog posts
5. Prefer recent results
</instructions>"""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_headers() -> dict[str, str]:
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": ("https://github.com/Shelpuk-AI-Technology-Consulting/web-search-mcp"),
        "X-Title": "web-search-mcp",
    }


def _build_search_tool(
    max_results: int,
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"engine": "native", "max_results": max_results}
    if allowed_domains:
        params["allowed_domains"] = allowed_domains
    if excluded_domains:
        params["excluded_domains"] = excluded_domains
    return [{"type": "openrouter:web_search", "parameters": params}]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _safe_get_citation_field(ann: dict[str, Any], field: str) -> str:
    val = ann.get(field, "")
    if val and isinstance(val, str):
        return val
    nested = ann.get("url_citation")
    if isinstance(nested, dict):
        nv = nested.get(field, "")
        if isinstance(nv, str):
            return nv
        if field == "content":
            nv = nested.get("snippet", nested.get("text", ""))
            if isinstance(nv, str):
                return nv
    return ""


def _extract_citations(message: dict[str, Any]) -> list[dict[str, str]]:
    annotations = message.get("annotations", [])
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for ann in annotations:
        if ann.get("type") != "url_citation":
            continue
        url = _safe_get_citation_field(ann, "url")
        if not url or url in seen:
            continue
        seen.add(url)
        citations.append(
            {
                "url": url,
                "title": _safe_get_citation_field(ann, "title"),
                "snippet": _safe_get_citation_field(ann, "content"),
            }
        )
    return citations


# ---------------------------------------------------------------------------
# Light Provider: returns WebSearchResult[] for RRF merge pipeline
# ---------------------------------------------------------------------------


class GrokProviderError(RuntimeError):
    pass


class GrokProviderConfigError(GrokProviderError):
    pass


def _check_grok_configured() -> None:
    if not settings.openrouter_api_key:
        raise GrokProviderConfigError(
            "OPENROUTER_API_KEY is not set. Configure it as an environment variable."
        )


async def search_grok_openrouter(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Light provider — extracts WebSearchResult[] from Grok native search.

    Uses OpenRouter's openrouter:web_search server tool with engine: "native"
    to route through xAI's infrastructure. Returns structured result list
    for RRF merge pipeline participation.

    NOTE: providers/provider_count are NOT set here — the orchestrator
    patches them via model_copy() in _search_single_provider().

    Args:
        query: Search query
        num_results: How many results to return (1-10)
        http_client: Optional shared httpx client

    Returns:
        List of WebSearchResult objects extracted from url_citation annotations
    """
    if not query or not query.strip():
        return []
    if num_results < 1:
        return []

    _check_grok_configured()
    headers = _get_headers()
    model = settings.grok_model or DEFAULT_MODEL
    system_prompt, _ = build_provider_grok_prompt(
        query=query,
        research_goal=None,
        provider_name="grok",
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"<query>{query}</query>"},
    ]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": _build_search_tool(num_results),
        "temperature": 0,
    }

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise GrokProviderError("OpenRouter response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise GrokProviderError("OpenRouter response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        choices = data.get("choices", [])
        if not choices:
            return []

        message = choices[0].get("message", {})
        citations = _extract_citations(message)

        results: list[WebSearchResult] = []
        for c in citations:
            title = c.get("title", "")
            link = c.get("url", "")
            snippet = c.get("snippet", "")
            if not title.strip() or not link.strip():
                continue
            if not snippet.strip():
                snippet = title
            results.append(WebSearchResult(title=title, link=link, snippet=snippet))
            if len(results) >= num_results:
                break

        return results

    with create_llm_operation_span(
        "search",
        system="openrouter",
        attributes={
            "llm.model_name": model,
            "search.query": query[:500],
            "search.num_results_requested": num_results,
        },
    ) as span:
        try:
            results = await run_provider(
                "grok_openrouter",
                query,
                num_results,
                request=_do_request,
                parse_response=_parse_response,
                http_client=http_client,
                timeout_seconds=REQUEST_TIMEOUT,
            )
        except Exception as exc:
            set_span_error(span, exc)
            raise

        span.set_attribute("search.source_count", len(results))
        set_span_success(span, result_count=len(results))
        return results


# ---------------------------------------------------------------------------
# Standalone Tool result model
# ---------------------------------------------------------------------------


@dataclass
class GrokSearchResult:
    """Result from the grok_search standalone tool."""

    query: str
    answer: str
    citations: list[dict[str, str]]
    model: str
    search_queries_used: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None

    @property
    def model_used(self) -> str:
        return self.model


# ---------------------------------------------------------------------------
# Standalone Tool: synthesized answer + citations
# ---------------------------------------------------------------------------


async def grok_search(
    query: str,
    research_goal: str,
    *,
    model: str | None = None,
    num_results: int = 5,
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
    timeout: float | None = None,
) -> GrokSearchResult:
    """Synthesized web+X search via Grok 4.3 on OpenRouter.

    Grok autonomously decides how many search passes to run. Both
    web_search and x_search tools are available; x_search fires when
    the query or research goal mentions X/Twitter or social media data.

    Args:
        query: Search query — be specific with keywords
        research_goal: What you need the results for (guides Grok's focus)
        model: Override default model ID (e.g. "x-ai/grok-4.3")
        num_results: Approximate citations to surface (1-10, default 5)
        allowed_domains: Optional domain allowlist for web search
        excluded_domains: Optional domain blocklist for web search
        timeout: HTTP timeout in seconds

    Returns:
        GrokSearchResult with synthesized answer, citations, and diagnostics
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")

    _check_grok_configured()
    resolved_model = model or settings.grok_model or DEFAULT_MODEL
    resolved_timeout = timeout or settings.grok_timeout_seconds or REQUEST_TIMEOUT
    system_prompt, user_prompt = build_provider_grok_prompt(
        query=query.strip(),
        research_goal=research_goal,
        provider_name="grok",
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    max_results = max(1, min(num_results, 10))
    tools = _build_search_tool(max_results, allowed_domains, excluded_domains)

    payload: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "tools": tools,
        "temperature": 0,
    }

    headers = _get_headers()
    with create_llm_operation_span(
        "chat",
        system="openrouter",
        attributes={
            "llm.model_name": resolved_model,
            "search.query": query[:500],
            "search.research_goal": research_goal[:500],
            "search.num_results_requested": max_results,
        },
    ) as span:
        async with httpx.AsyncClient(timeout=resolved_timeout) as client:
            try:
                resp = await client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=resolved_timeout,
                )
                resp.raise_for_status()
            except httpx.TimeoutException as e:
                set_span_error(span, e)
                raise httpx.HTTPError(f"Grok search timed out after {resolved_timeout}s") from e
            except httpx.HTTPStatusError as e:
                set_span_error(span, e)
                raise e

            try:
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    set_span_success(span, result_count=0)
                    return GrokSearchResult(
                        query=query,
                        answer="",
                        citations=[],
                        model=resolved_model,
                        search_queries_used=0,
                        error="No response from model",
                    )

                message = choices[0].get("message", {})
                answer = _extract_text(message)
                citations = _extract_citations(message)
                usage = data.get("usage", {})
                server_tool_use = usage.get("server_tool_use", {})
                token_usage = extract_llm_usage(usage)

                search_queries = server_tool_use.get("web_search_requests", 0)

                span.set_attribute("search.citation_count", len(citations))
                span.set_attribute("search.answer_chars", len(answer))
                span.set_attribute("search.web_search_requests", search_queries)
                if token_usage:
                    if token_usage.input_tokens is not None:
                        span.set_attribute("llm.token_count.prompt", token_usage.input_tokens)
                    if token_usage.output_tokens is not None:
                        span.set_attribute("llm.token_count.completion", token_usage.output_tokens)
                    if token_usage.total_tokens is not None:
                        span.set_attribute("llm.token_count.total", token_usage.total_tokens)
                set_span_success(span, result_count=len(citations))
                return GrokSearchResult(
                    query=query,
                    answer=answer,
                    citations=citations,
                    model=data.get("model", resolved_model),
                    search_queries_used=search_queries,
                    input_tokens=token_usage.input_tokens if token_usage else None,
                    output_tokens=token_usage.output_tokens if token_usage else None,
                )
            except Exception as exc:
                set_span_error(span, exc)
                raise
