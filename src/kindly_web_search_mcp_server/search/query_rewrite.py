"""Query rewrite: expand queries via Mistral LLM when no precision signals detected.

Simple flow:
1. Detect precision signals → bypass (return original only)
2. No signals → expand via Mistral with docs/issues angles
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
from ..telemetry import (
    record_query_rewrite,
    REWRITE_POLICY,
    REWRITE_VARIANT_COUNT,
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

    kind: Literal["original", "official_docs", "community_issues"]
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
You are a expert query optimizer, your job is to rewrite messy coding-related search queries into a small set of better web-search queries.

Return JSON only.
Follow the schema exactly.

Task:
- You receive ONE raw query string from a coding agent.
- The input may be a bag of words, missing punctuation, or badly phrased.
- Produce 2 or 3 complementary web-search queries.

Hard rules:
1. Keep one query very close to the original intent.
2. Preserve exact technical literals when present:
   package names, versions, CLI flags, repo names, model names,
   function/class names, file paths, exact error fragments, quoted text.
3. Do not invent package names, versions, issue numbers, or APIs.
4. Do not over-interpret vague queries. Clean them up, but stay conservative.
5. Make the variants complementary, not near-duplicates.

Preferred variants:
- original: cleaned, minimal rewrite, closest to the raw query
- official_docs: docs / API reference / migration guide / release notes angle
- community_issues: GitHub issues / discussions / Stack Overflow / workaround angle

When the raw query already explicitly targets one of those angles, still return complementary variants if possible.

Good examples:

Input:
langchain agent react 2024 2025

Output:
{
  "variants": [
    {
      "kind": "original",
      "query": "langchain react agent 2024 2025",
      "why": "Closest cleaned version of the original keyword query."
    },
    {
      "kind": "official_docs",
      "query": "LangChain ReAct agent docs 2024 2025",
      "why": "Targets official documentation and current guidance."
    },
    {
      "kind": "community_issues",
      "query": "LangChain ReAct agent GitHub issue discussion 2024 2025",
      "why": "Targets implementation problems and community discussions."
    }
  ]
}

Input:
pydantic undefined import error v2 fastapi

Output:
{
  "variants": [
    {
      "kind": "original",
      "query": "pydantic Undefined import error v2 FastAPI",
      "why": "Keeps the exact error terms and involved libraries."
    },
    {
      "kind": "official_docs",
      "query": "Pydantic v2 migration Undefined import FastAPI",
      "why": "Targets migration or API-change documentation."
    },
    {
      "kind": "community_issues",
      "query": "Pydantic Undefined import FastAPI GitHub issue workaround",
      "why": "Targets bug reports and fixes."
    }
  ]
}

Input:
crewai memory sqlite chroma best practice

Output:
{
  "variants": [
    {
      "kind": "original",
      "query": "CrewAI memory sqlite chroma best practices",
      "why": "Closest cleaned version of the original keyword query."
    },
    {
      "kind": "official_docs",
      "query": "CrewAI memory documentation sqlite chroma",
      "why": "Targets framework documentation and official examples."
    },
    {
      "kind": "community_issues",
      "query": "CrewAI memory sqlite chroma GitHub discussion issue",
      "why": "Targets practitioner discussion and troubleshooting."
    }
  ]
}

Bad behavior:
- turning a vague query into a highly specific claim
- inventing a library version not present in the input
- rewriting away exact literals
- returning paragraphs instead of concise search queries
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
    diagnostics: Diagnostics | None = None,
) -> QueryRewritePlan:
    """Rewrite query via Mistral if no precision signals detected.

    Flow:
    1. Detect precision signals → bypass (return original only)
    2. No signals → expand via Mistral with docs/issues angles
    3. Mistral failure → fallback to original

    Args:
        query: Raw query string
        diagnostics: Optional diagnostics emitter

    Returns:
        QueryRewritePlan with final queries to execute
    """
    start_time = time.time()
    normalized_query = normalize_query(query)

    policy = await resolve_query_routing(query, diagnostics=diagnostics)
    max_variants = max(1, min(settings.query_rewrite_max_variants, 3))

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
            return _fallback_plan(query, policy, "No Mistral API key configured.")

        if diagnostics:
            diagnostics.emit(
                "query_rewrite.start",
                "Starting Mistral query rewrite",
                {
                    "query": query,
                    "policy": policy.mode,
                    "must_keep_terms": policy.must_keep_terms,
                    "model": settings.query_rewrite_model,
                    "max_variants": max_variants,
                },
            )

        user_prompt = f"Raw query: {normalize_query(query)}"

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
                    "variant.type": getattr(variant, 'type', 'unknown'),
                    "variant.text": getattr(variant, 'text', str(variant))[:100],
                })

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

            if diagnostics:
                diagnostics.emit(
                    "query_rewrite.fallback",
                    "Mistral query rewrite failed; using original query",
                    {"error": type(exc).__name__, "detail": str(exc)},
                )
            return _fallback_plan(
                query, policy, "Rewrite failed; original query preserved."
            )
