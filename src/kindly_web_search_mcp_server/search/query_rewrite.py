"""Query rewrite: expand queries via LiteLLM Router with multi-provider load distribution.

Providers (free-tier load distribution):
- Mistral: mistral-small-2603
- Cerebras: llama3.1-8b
- Groq: llama-3.1-8b-instant

Router handles:
- RPM-weighted load distribution (simple-shuffle strategy)
- Automatic failover on rate limit errors
- Cooldowns (30s after 3 consecutive failures)
- Retries (2 attempts per request)
"""

from __future__ import annotations

import asyncio
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

# LiteLLM Router for multi-provider load distribution
try:
    from litellm.router import Router
    LITELLM_AVAILABLE = True
except ImportError:
    Router = None  # type: ignore[misc,assignment]
    LITELLM_AVAILABLE = False

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")

# Singleton router instance (lazy initialization)
_ROUTER: Any = None  # Router | None


def _build_query_rewrite_router() -> Any:
    """Build LiteLLM Router with multi-provider configuration for query rewrite.

    Providers (free-tier load distribution):
    - Mistral: mistral/mistral-small-2603
    - Cerebras: cerebras/llama3.1-8b
    - Groq: groq/llama-3.1-8b-instant

    Router handles:
    - RPM-weighted load distribution (simple-shuffle strategy)
    - Automatic failover on rate limit errors
    - Cooldowns (30s after 3 consecutive failures)
    - Retries (2 attempts per request)
    """
    if not LITELLM_AVAILABLE or Router is None:
        logger.debug("LiteLLM not available, query rewrite disabled")
        return None

    model_list = []

    # Mistral (existing provider)
    if settings.mistral_api_key:
        model_list.append({
            "model_name": "query-rewrite",
            "litellm_params": {
                "model": f"mistral/{settings.query_rewrite_model}",  # mistral-small-2603
                "api_key": settings.mistral_api_key,
                "rpm": settings.query_rewrite_mistral_rpm,
            }
        })

    # Cerebras (free tier, fast inference)
    if settings.cerebras_api_key:
        model_list.append({
            "model_name": "query-rewrite",
            "litellm_params": {
                "model": "cerebras/llama3.1-8b",
                "api_key": settings.cerebras_api_key,
                "rpm": settings.query_rewrite_cerebras_rpm,
            }
        })

    # Groq (free tier, fastest inference)
    if settings.groq_api_key:
        model_list.append({
            "model_name": "query-rewrite",
            "litellm_params": {
                "model": "groq/llama-3.1-8b-instant",
                "api_key": settings.groq_api_key,
                "rpm": settings.query_rewrite_groq_rpm,
            }
        })

    if not model_list:
        logger.debug("No query rewrite API keys configured")
        return None

    logger.info(
        "Query rewrite router initialized with %d providers: %s",
        len(model_list),
        [m["litellm_params"]["model"] for m in model_list]
    )

    return Router(
        model_list=model_list,
        routing_strategy="simple-shuffle",  # RPM-weighted random selection
        num_retries=2,
        retry_after=1,  # seconds (int required)
        allowed_fails=3,
        cooldown_time=30,
    )


def _get_router() -> Any:
    """Get or initialize the singleton router instance."""
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = _build_query_rewrite_router()
    return _ROUTER


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
    """Output from LLM query rewrite."""

    variants: list[QueryVariant] = Field(
        description="Two or three complementary search queries."
    )


class QueryRewritePlan(BaseModel):
    """Final plan for query execution."""

    original_query: str
    policy: RewritePolicy
    variants: list[QueryVariant]
    final_queries: list[str]


# System prompt: Role + Constraints + Output Schema (concise for smaller models)
# Research: Llama 3.1 8B needs explicit schema + enum constraints (50% parsing improvement)
QUERY_REWRITE_SYSTEM_PROMPT = """You are a query optimizer for web search.

Your job: Transform keyword-dump queries into clean, diverse search variants.

OUTPUT FORMAT (JSON only, no other text):
{"variants": [{"kind": "...", "query": "...", "why": "..."}]}

kind must be one of: original, docs, community, keywords
- original: Cleaned version of input query (remove filler, keep core terms)
- docs: Add documentation terms (API, guide, reference, documentation)
- community: Add community terms (GitHub issue, Stack Overflow, solution)
- keywords: Terse extraction (only entity names + noun/verb base forms)

CONSTRAINTS:
- Preserve exact literals: packages, versions, error codes, URLs, quoted text
- Never invent facts not in input or research_goal
- Remove filler words: what, how, does, can, should, the, a, for, with, to
- Strip keyword pile-on: if 6+ words, reduce to 3-4 core terms
- Each variant must target different source type"""


def _build_user_prompt(query: str, research_goal: str | None, intent: str) -> str:
    """Build user prompt with few-shot examples for smaller models.

    Research findings:
    - Few-shot prompting CRITICAL for Llama 3.1 8B (FabWebStudio)
    - Philipp Schmid: Enum constraints improve JSON parsing by 50%
    - Llama docs: Examples in user prompt (model trained that way)
    - Imperative instructions work better: "Now optimize" not "Can you optimize"
    """

    # Few-shot examples block (critical for smaller model instruction following)
    examples_block = """
EXAMPLES:

Input query: "react 18.2.0 hooks changelog release notes documentation API"
Research goal: "Find React 18.2.0 changelog to check hooks support"
Output:
{"variants": [
  {"kind": "original", "query": "react 18.2.0 changelog hooks", "why": "Reduced keyword pile, kept version"},
  {"kind": "docs", "query": "react 18.2.0 hooks documentation", "why": "Official docs target"},
  {"kind": "community", "query": "react 18.2.0 hooks GitHub", "why": "Community discussions"}
]}

Input query: "TypeError Cannot read property undefined JavaScript fix error solution"
Research goal: "Debug TypeError in JavaScript code"
Output:
{"variants": [
  {"kind": "original", "query": "TypeError Cannot read property undefined JavaScript", "why": "Kept exact error, removed filler"},
  {"kind": "docs", "query": "JavaScript TypeError property access docs", "why": "MDN documentation"},
  {"kind": "community", "query": "TypeError Cannot read property undefined Stack Overflow", "why": "Community solutions"}
]}

Input query: "langchain agent memory sqlite chromadb tutorial guide how to"
Research goal: "Implement LangChain agent with memory backend"
Output:
{"variants": [
  {"kind": "original", "query": "langchain agent memory sqlite chroma", "why": "Normalized chromadb, removed tutorial/guide"},
  {"kind": "docs", "query": "langchain memory sqlite documentation", "why": "Official integration docs"},
  {"kind": "community", "query": "langchain memory sqlite chroma GitHub issue", "why": "Implementation problems"}
]}

Input query: "best payment gateway for SaaS startups Europe pricing comparison"
Research goal: "Compare payment gateway options for European SaaS"
Output:
{"variants": [
  {"kind": "original", "query": "best payment gateway SaaS Europe", "why": "Kept core intent, removed filler"},
  {"kind": "docs", "query": "payment gateway comparison Stripe PayPal Europe SaaS", "why": "Added comparison entities"},
  {"kind": "community", "query": "SaaS payment gateway Europe Reddit pricing", "why": "Community recommendations"}
]}

---
"""

    # Task block with current input (imperative instruction for smaller models)
    task_block = f"""Now optimize this query:

Input query: {query}
Intent: {intent}"""

    if research_goal:
        task_block += f"""
Research goal: {research_goal}"""

    task_block += """
Output:"""

    return examples_block + task_block


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
    """Postprocess LLM output: dedupe, limit variants, build plan."""
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
    """Rewrite query via LiteLLM Router if no precision signals detected.

    Flow:
    1. Detect precision signals → bypass (return original only)
    2. No signals → expand via Router with docs/issues angles
    3. Router failure → fallback to original

    Args:
        query: Raw query string
        intent: Client-provided intent (NOT classified by LLM).
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

        # Get router (multi-provider load distribution)
        router = _get_router()
        if router is None:
            # Record telemetry for missing router
            duration = time.time() - start_time
            record_query_rewrite(
                policy="fallback",
                variant_count=1,
                has_precision_signals=False,
                duration_seconds=duration,
                model="fallback",
            )
            span.add_event("rewrite.fallback", attributes={"reason": "No router available"})
            emit_observability_event(
                logger,
                "query.rewrite.fallback",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                reason="LiteLLM Router not available or no API keys configured.",
                final_queries=[normalized_query],
            )
            return _fallback_plan(query, policy, "LiteLLM Router not available or no API keys configured.")

        if diagnostics:
            diagnostics.emit(
                "query_rewrite.start",
                "Starting LiteLLM Router query rewrite",
                {
                    "query": query,
                    "policy": policy.mode,
                    "intent": intent,
                    "must_keep_terms": policy.must_keep_terms,
                    "max_variants": max_variants,
                },
            )

        # Build user prompt with few-shot examples (research: critical for smaller models)
        user_prompt = _build_user_prompt(
            query=normalized_query,
            research_goal=research_goal,
            intent=intent
        )

        try:
            # LiteLLM Router handles provider selection, retries, and failover
            # Temperature 0.8 per QueryGym research for keyword extraction
            response = await asyncio.wait_for(
                router.acompletion(
                    model="query-rewrite",  # Router selects provider based on RPM weights
                    messages=[
                        {
                            "role": "system",
                            "content": QUERY_REWRITE_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=settings.query_rewrite_temperature,
                    response_format={"type": "json_object"},  # Force JSON output
                ),
                timeout=settings.query_rewrite_timeout_seconds,
            )

            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("Expected string JSON content from LLM")

            parsed = QueryRewriteOutput.model_validate(json.loads(content))
            plan = _postprocess(query, parsed, policy, max_variants=max_variants)

            # Get the actual model used from response
            used_model = response.model or "unknown"

            # Record telemetry for successful expand
            duration = time.time() - start_time
            record_query_rewrite(
                policy="expand",
                variant_count=len(plan.final_queries),
                has_precision_signals=False,
                duration_seconds=duration,
                model=used_model,
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
                model=used_model,
            )

            if diagnostics:
                diagnostics.emit(
                    "query_rewrite.result",
                    "LiteLLM Router query rewrite completed",
                    {"queries": plan.final_queries, "policy": plan.policy.mode, "model": used_model},
                )

            return plan
        except (
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
                    "LiteLLM Router query rewrite failed; using original query",
                    {"error": type(exc).__name__, "detail": str(exc)},
                )
            return _fallback_plan(
                query, policy, "Rewrite failed; original query preserved."
            )