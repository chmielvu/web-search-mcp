"""Query rewrite: expand queries via Mistral LLM when no precision signals detected.

Simple flow:
1. Detect precision signals → bypass (return original only)
2. No signals → expand via Mistral with docs/issues angles
3. Uses CoT prompting - model thinks through restructuring before generating
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..settings import settings
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from ..telemetry import (
    record_query_rewrite,
    record_query_length,
    REWRITE_POLICY,
    REWRITE_MODEL,
    SEARCH_QUERY,
)
from .normalize import normalize_query
from .query_policy import RewritePolicy
from .query_policy_resolver import resolve_query_routing
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _load_mistral_client_class():
    """Load the Mistral client lazily so import-time failures do not break search.

    Some environments run tests or lightweight local tooling without the full
    Mistral SDK installed, or with a namespace package that does not expose the
    generated `mistralai.client` module. Query rewrite should degrade to the
    original query in those cases rather than fail module import/collection.
    """
    candidate_modules = ("mistralai.client", "mistralai")
    for module_name in candidate_modules:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        client_cls = getattr(module, "Mistral", None)
        if client_cls is not None:
            return client_cls
    raise ImportError(
        "Could not import Mistral client from mistralai.client or mistralai"
    )


class QueryVariant(BaseModel):
    """A single query variant for web search."""

    kind: Literal[
        # Code intent variants
        "original",
        "official_docs",
        "community_issues",
        # General research intent variants
        "expanded",
        "focused",
        # Comparison intent variants
        "entity_a",
        "entity_b",
    ]
    query: str = Field(
        description=(
            "A concise web search query. Preserve exact technical literals: package names, "
            "versions, CLI flags, repo names, class/function names, model names, file paths, "
            "quoted strings, and exact error fragments."
        )
    )
    why: str = Field(description="Short reason for this query variant.")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = normalize_query(value)
        if not value:
            raise ValueError("query cannot be empty")
        return value


class QueryRewriteOutput(BaseModel):
    """Output from Mistral query rewrite."""

    variants: list[QueryVariant] = Field(
        description="Two or three complementary search queries."
    )


class QueryRewritePlan(BaseModel):
    """Final plan for query execution."""

    original_query: str
    policy: RewritePolicy
    variants: list[QueryVariant]
    final_queries: list[str]


MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT = """
You are a query optimizer for a coding assistant's web search tool.

CORE TASK: Take an over-keyworded or messy query and produce 3 concise, diverse search queries.

Return JSON only.
Follow the schema exactly.

CRITICAL INSTRUCTION FROM RESEARCH:
"Strip out all information that is not relevant for the retrieval task"
- Reduce keyword pile-on (agents dump 10+ keywords)
- Keep only terms that meaningfully impact search results
- Preserve exact technical literals verbatim: package names, versions, CLI flags, repo names, model names, function/class names, file paths, exact error fragments, quoted text.

CHAIN-OF-THINK PROCESS (think before generating):
1. Analyze: What is the core intent? Identify key technical terms vs filler keywords.
2. Strip: Remove redundant keywords, fix typos, normalize library names.
3. Generate: Produce 3 variants with different vocabulary/perspective/source focus.

Generate 3 diverse search queries focusing on:
- Different vocabulary: Use synonyms, related technical terms
- Different perspectives: User language vs expert/documentation language
- Different sources: Target docs sites, GitHub issues, tutorials

Query types (for intent="code"):
- original: Strip irrelevant keywords, fix typos, restructure for clarity
- official_docs: Target documentation sites (docs.*, API references)
- community_issues: Target GitHub issues, Stack Overflow, discussions

IMPORTANT: For web search, keep queries CONCISE (under 60 characters ideal). Do NOT expand into paragraphs.

Good examples (showing chain-of-think process):

Input: "query reformulation web search LLM best practices 2025 2026 fanout expansion agentic"
Chain-of-think: Core intent is "LLM query reformulation for web search", years 2025-2026 are relevant, "fanout expansion agentic" are filler keywords. Strip these, normalize to essential terms.
Output:
{
  "variants": [
    {"kind": "original", "query": "LLM query reformulation web search 2025", "why": "Reduced from 10 keywords to 4 core terms"},
    {"kind": "official_docs", "query": "LLM query reformulation documentation", "why": "Docs perspective, expert vocabulary"},
    {"kind": "community_issues", "query": "LLM query reformulation GitHub discussion", "why": "Community sources, problem-focused"}
  ]
}

Input: "TypeError Cannot read property undefined JavaScript fix error"
Chain-of-think: Exact error terms "TypeError Cannot read property undefined" are precision signals, "fix error" are redundant filler (already implied). Remove filler, keep exact error.
Output:
{
  "variants": [
    {"kind": "original", "query": "TypeError Cannot read property undefined JavaScript", "why": "Kept exact error terms, removed filler"},
    {"kind": "official_docs", "query": "JavaScript TypeError property access docs", "why": "Docs vocabulary: property access instead of undefined"},
    {"kind": "community_issues", "query": "TypeError Cannot read property undefined Stack Overflow", "why": "Community sources with exact error"}
  ]
}

Input: "langchain agent memory sqlite chromadb tutorial guide"
Chain-of-think: "langchain agent memory" is core intent, "sqlite chromadb" are specific backends, "tutorial guide" are redundant keywords. Remove tutorial/guide, normalize chromadb to chroma.
Output:
{
  "variants": [
    {"kind": "original", "query": "LangChain agent memory sqlite chroma", "why": "Normalized chromadb to chroma, removed tutorial/guide"},
    {"kind": "official_docs", "query": "LangChain memory documentation sqlite integration", "why": "Docs perspective, official sources"},
    {"kind": "community_issues", "query": "LangChain memory sqlite chroma GitHub issue", "why": "Community sources, implementation problems"}
  ]
}

Input: "pydantic undefined import error v2 fastapi"
Chain-of-think: Error terms "pydantic undefined import error v2 fastapi" are all relevant. Version v2 is precision signal, FastAPI is context. Keep all, add docs/community angles.
Output:
{
  "variants": [
    {"kind": "original", "query": "Pydantic Undefined import error v2 FastAPI", "why": "Kept exact error terms and involved libraries"},
    {"kind": "official_docs", "query": "Pydantic v2 migration Undefined import FastAPI", "why": "Targets migration or API-change documentation"},
    {"kind": "community_issues", "query": "Pydantic Undefined import FastAPI GitHub issue workaround", "why": "Targets bug reports and fixes"}
  ]
}

Non-code examples (intent="general_research"):
Input: "best payment gateway for SaaS startups Europe"
Chain-of-think: Core intent is payment gateway comparison for SaaS in Europe. Remove "for" (noise), keep geographic scope.
Output:
{
  "variants": [
    {"kind": "original", "query": "best payment gateway SaaS Europe", "why": "Kept core intent, minimal change"},
    {"kind": "expanded", "query": "payment gateway comparison Stripe PayPal Europe SaaS", "why": "Broader context, added comparison entities"},
    {"kind": "focused", "query": "SaaS payment gateway Europe pricing fees", "why": "Narrowed to practical considerations"}
  ]
}

Comparison examples (intent="comparison"):
Input: "React vs Vue performance 2025"
Chain-of-think: Comparison structure with two entities (React, Vue), topic (performance), time scope (2025). Already clean, generate entity-specific variants.
Output:
{
  "variants": [
    {"kind": "original", "query": "React vs Vue performance 2025", "why": "Kept comparison structure intact"},
    {"kind": "entity_a", "query": "React performance benchmark 2025", "why": "Focus on React side for depth"},
    {"kind": "entity_b", "query": "Vue performance benchmark 2025", "why": "Focus on Vue side for depth"}
  ]
}

Bad behavior:
- turning a vague query into a highly specific claim
- inventing a library version not present in the input
- rewriting away exact literals (error codes, package names, versions)
- returning paragraphs instead of concise search queries
- adding more keywords instead of reducing keyword pile-on
""".strip()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _norm_key(text: str) -> str:
    return _normalize_ws(text).casefold()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _norm_key(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _fallback_plan(query: str, policy: RewritePolicy, reason: str) -> QueryRewritePlan:
    """Create a fallback plan with just the original query."""
    cleaned = normalize_query(query)
    original = QueryVariant(kind="original", query=cleaned, why=reason)
    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=[original],
        final_queries=[cleaned],
    )


def _postprocess(
    query: str, raw: QueryRewriteOutput, policy: RewritePolicy, max_variants: int
) -> QueryRewritePlan:
    """Postprocess Mistral output: dedupe, limit variants, build plan."""
    cleaned_original = normalize_query(query)
    final_queries = _dedupe_keep_order(
        [cleaned_original, *(variant.query for variant in raw.variants)]
    )
    final_queries = final_queries[:max_variants]

    variants: list[QueryVariant] = [
        QueryVariant(
            kind="original",
            query=cleaned_original,
            why="Original query preserved as a search candidate.",
        )
    ]
    for query_variant in final_queries[1:]:
        match = next(
            (
                variant
                for variant in raw.variants
                if _norm_key(variant.query) == _norm_key(query_variant)
            ),
            None,
        )
        if match:
            variants.append(match)

    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=variants,
        final_queries=final_queries,
    )


async def rewrite_search_query(
    query: str,
    *,
    intent: Literal["code", "general_research", "comparison"] = "code",
    diagnostics: Diagnostics | None = None,
    research_goal: str | None = None,
) -> QueryRewritePlan:
    """Rewrite query via Mistral if no precision signals detected.

    Flow:
    1. Detect precision signals → bypass (return original only)
    2. No signals → expand via Mistral with docs/issues angles
    3. Mistral failure → fallback to original

    Args:
        query: Raw query string
        intent: Client-provided intent (NOT classified by Mistral).
            "code" → original, official_docs, community_issues variants
            "general_research" → original, expanded, focused variants
            "comparison" → original, entity_a, entity_b variants
        diagnostics: Optional diagnostics emitter
        research_goal: Optional context/goal from client to guide query optimization

    Returns:
        QueryRewritePlan with final queries to execute
    """
    start_time = time.time()
    normalized_query = normalize_query(query)

    policy = await resolve_query_routing(query, diagnostics=diagnostics)
    max_variants = max(1, min(settings.query_rewrite_max_variants, 3))

    # Record query length for keyword pile-on detection (P2-1 metric)
    record_query_length(len(query), policy=policy.mode)

    # Create telemetry span
    with tracer.start_as_current_span(
        "query.rewrite",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: normalized_query[:500],
            REWRITE_POLICY: policy.mode,
            REWRITE_MODEL: settings.query_rewrite_model,
        },
    ) as span:
        span.set_attribute("rewrite.has_precision_signals", str(policy.mode == "bypass").lower())

        if not settings.query_rewrite_enabled:
            # Record telemetry for disabled rewrite
            duration = time.time() - start_time
            record_query_rewrite(
                policy="bypass",
                variant_count=1,
                has_precision_signals=False,
                duration_seconds=duration,
                model="disabled",
            )
            span.add_event("rewrite.disabled", attributes={"reason": "Query rewriting disabled"})
            emit_observability_event(
                logger,
                "query.rewrite.disabled",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                reason="Query rewriting disabled",
            )
            return _fallback_plan(query, policy, "Query rewriting disabled.")

        if policy.mode == "bypass":
            # Record telemetry for bypass
            duration = time.time() - start_time
            record_query_rewrite(
                policy="bypass",
                variant_count=1,
                has_precision_signals=True,
                duration_seconds=duration,
                model="bypass",
            )
            span.add_event("rewrite.bypass", attributes={"reason": policy.reason[:100]})
            emit_observability_event(
                logger,
                "query.rewrite.bypass",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                reason=policy.reason,
                final_queries=[normalized_query],
            )
            return _fallback_plan(query, policy, policy.reason)

        if not settings.mistral_api_key:
            # Record telemetry for missing API key
            duration = time.time() - start_time
            record_query_rewrite(
                policy="fallback",
                variant_count=1,
                has_precision_signals=False,
                duration_seconds=duration,
                model="fallback",
            )
            span.add_event("rewrite.fallback", attributes={"reason": "No Mistral API key"})
            emit_observability_event(
                logger,
                "query.rewrite.fallback",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                reason="No Mistral API key configured.",
                final_queries=[normalized_query],
            )
            return _fallback_plan(query, policy, "No Mistral API key configured.")

        if diagnostics:
            diagnostics.emit(
                "query_rewrite.start",
                "Starting Mistral query rewrite",
                {
                    "query": query,
                    "policy": policy.mode,
                    "intent": intent,
                    "must_keep_terms": policy.must_keep_terms,
                    "model": settings.query_rewrite_model,
                    "max_variants": max_variants,
                },
            )

        # Include intent in user prompt so Mistral knows which variant types to generate
        intent_context = ""
        if intent == "general_research":
            intent_context = " (intent: general_research - use original, expanded, focused variants)"
        elif intent == "comparison":
            intent_context = " (intent: comparison - use original, entity_a, entity_b variants)"
        # Default "code" intent uses original, official_docs, community_issues variants

        # Include research_goal if provided by client
        goal_context = ""
        if research_goal:
            goal_context = f"\n\nResearch goal from user: {research_goal}"

        user_prompt = f"Raw query: {normalize_query(query)}{intent_context}{goal_context}"

        try:
            mistral_client_cls = _load_mistral_client_class()
            async with mistral_client_cls(api_key=settings.mistral_api_key) as client:
                response = await asyncio.wait_for(
                    client.chat.complete_async(
                        model=settings.query_rewrite_model,
                        messages=[
                            {
                                "role": "system",
                                "content": MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT,
                            },
                            {"role": "user", "content": user_prompt},
                        ],
                        stream=False,
                        response_format={"type": "json_object"},
                        temperature=settings.query_rewrite_temperature,
                    ),
                    timeout=settings.query_rewrite_timeout_seconds,
                )

            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("Expected string JSON content from Mistral")

            parsed = QueryRewriteOutput.model_validate(json.loads(content))
            plan = _postprocess(query, parsed, policy, max_variants=max_variants)

            # Record telemetry for successful expand
            duration = time.time() - start_time
            record_query_rewrite(
                policy="expand",
                variant_count=len(plan.final_queries),
                has_precision_signals=False,
                duration_seconds=duration,
                model=settings.query_rewrite_model,
            )

            # Add variants as span events
            for i, variant in enumerate(plan.variants[:5]):
                span.add_event(f"rewrite.variant.{i}", attributes={
                    "variant.kind": variant.kind,
                    "variant.query": variant.query[:100],
                    "variant.why": variant.why[:100] if variant.why else "",
                })

            emit_observability_event(
                logger,
                "query.rewrite.completed",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                intent=intent,
                research_goal=research_goal,
                final_queries=plan.final_queries,
                variants=[variant.model_dump() for variant in plan.variants],
                duration_ms=round(duration * 1000, 3),
                model=settings.query_rewrite_model,
            )

            if diagnostics:
                diagnostics.emit(
                    "query_rewrite.result",
                    "Mistral query rewrite completed",
                    {"queries": plan.final_queries, "policy": plan.policy.mode},
                )

            return plan
        except (
            ImportError,
            TimeoutError,
            ValidationError,
            ValueError,
            json.JSONDecodeError,
            IndexError,
        ) as exc:
            logger.warning("Query rewrite failed, using original query: %s", exc)

            # Record telemetry for failed rewrite
            duration = time.time() - start_time
            record_query_rewrite(
                policy="fallback",
                variant_count=1,
                has_precision_signals=False,
                duration_seconds=duration,
                model="error",
            )

            span.add_event("rewrite.error", attributes={
                "error.type": type(exc).__name__,
                "error.message": str(exc)[:100],
            })
            emit_observability_event(
                logger,
                "query.rewrite.error",
                level=logging.WARNING,
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                intent=intent,
                research_goal=research_goal,
                error_type=type(exc).__name__,
                error_message=str(exc),
                final_queries=[normalized_query],
            )

            if diagnostics:
                diagnostics.emit(
                    "query_rewrite.fallback",
                    "Mistral query rewrite failed; using original query",
                    {"error": type(exc).__name__, "detail": str(exc)},
                )
            return _fallback_plan(
                query, policy, "Rewrite failed; original query preserved."
            )
