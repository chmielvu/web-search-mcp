"""Fail-open GLiNER2 and worker-LLM enrichment for code-query plans."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...entity.gliner_client import QueryFeatureAnalysis, get_gliner_client
from ...inference.router import build_worker_router
from .models import CodeSearchRequest
from .query import QueryPlan

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:/+\-]{2,79}$")
_GENERIC = {
    "code",
    "example",
    "examples",
    "find",
    "handling",
    "implementation",
    "implementations",
    "real",
    "search",
    "show",
}
_RESOLUTION_MIN_CONFIDENCE = 0.70
_REPOSITORY_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _valid_repository_ref(value: str) -> str | None:
    candidate = value.strip().strip("\"'")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if candidate.casefold().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.strip("/")
    return candidate if _REPOSITORY_REF.fullmatch(candidate) else None


_OPTIMIZER_SYSTEM = """You optimize natural-language requests for public source-code retrieval.
Return only the requested typed object. Emit source strings that could occur verbatim in code:
API endpoint fragments, header values, method calls, qualified symbols, and conservative identifier
spellings. Never emit provider query syntax or invent repositories, paths, or libraries. Generic prose
such as find, show, real, code, implementation, example, handling, and search is not a useful variant.
GitHub GraphQL discovers repositories/issues/discussions but does not search code; GitHub code search
uses REST /search/code. The user message includes a separately labeled research goal; use it as
intent context, never as provider syntax. Keep each list short and high precision.

The exa_semantic_query field is a natural-language query for Exa's /context semantic code retrieval
endpoint. It should rephrase the user's intent as a self-contained search for code examples: include
the key API name, language, library, or pattern, and the goal (e.g. "FastMCP tool registration with
annotated arguments in Python" not just "FastMCP"). Do not include provider syntax (no repo:, lang:,
file:). Keep it under 2000 characters."""


class CodeQueryOptimization(BaseModel):
    """Validated engine-neutral suggestions from the existing worker chain."""

    model_config = ConfigDict(extra="forbid")

    objective: Literal[
        "exact",
        "symbol",
        "usage",
        "implementation",
        "repository_discovery",
        "documentation",
    ]
    exact_phrases: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    api_literals: list[str] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)
    exa_semantic_query: str = Field(default="", description="A natural-language semantic query optimized for Exa's /context code retrieval endpoint.")
    rationale: str = ""


def _needs_worker(plan: QueryPlan, request: CodeSearchRequest) -> bool:
    if plan.regex_source or request.repositories:
        return False
    if plan.source_tokens:
        return False
    return request.mode in {"docs", "discovery"}


def _rewrite_user_message(query: str, research_goal: str | None) -> str:
    goal = " ".join((research_goal or "").split()).strip()[:500] or query.strip()
    return (
        "Search request:\n"
        f"{query.strip()}\n\n"
        "Research goal (context only; do not emit it as a query term):\n"
        f"{goal}"
    )


async def _worker_optimization(
    query: str, research_goal: str | None = None
) -> CodeQueryOptimization:
    generation = await build_worker_router().complete_json(
        messages=[
            {"role": "system", "content": _OPTIMIZER_SYSTEM},
            {"role": "user", "content": _rewrite_user_message(query, research_goal)},
        ],
        response_model=CodeQueryOptimization,
        temperature=0.0,
        timeout_seconds=6.0,
        reasoning_effort="none",
        operation="code_search.query_optimize",
    )
    return CodeQueryOptimization.model_validate_json(generation.content)


def _valid_variant(value: str) -> str | None:
    candidate = value.strip().strip("\"'")
    if not candidate or len(candidate) > 80 or candidate.casefold() in _GENERIC:
        return None
    return candidate if _IDENTIFIER.fullmatch(candidate) else None


def _merge_enrichment(
    plan: QueryPlan,
    features: QueryFeatureAnalysis | None,
    optimization: CodeQueryOptimization | None,
    failures: list[str],
) -> QueryPlan:
    # Keep deterministic user-intent variants ahead of model suggestions.
    # Enrichment may add high-signal identifiers, but it must not replace the
    # query the caller actually asked for.
    qualifiers = list(plan.qualifiers)
    variants: list[tuple[str, str]] = []
    variants.extend(plan.variant_pairs)
    anchors = list(plan.anchor_terms)
    warnings = list(plan.warnings)
    library_hint = plan.library_hint
    repository_hint = plan.repository_hint
    resolution_source = plan.resolution_source

    if "code search" in plan.original_query.casefold():
        variants.extend(
            [("/search/code", "lexical"), ("search_code", "symbol"), ("code_search", "symbol")]
        )
    if features is not None:
        for entity in features.entities:
            value = _valid_variant(entity.text)
            if value and entity.label in {"api_function", "error_class", "env_var", "model_id"}:
                variants.append((value, "symbol"))
            if entity.label == "language" and not any(key == "language" for key, _ in qualifiers):
                qualifiers.append(("language", entity.text))
            entity_confidence = (
                entity.confidence
                if entity.confidence is not None
                else features.confidence
            )
            if entity_confidence >= _RESOLUTION_MIN_CONFIDENCE:
                if entity.label == "package" and value and library_hint is None:
                    library_hint = value
                    resolution_source = "gliner2"
                    warnings.append(f"GLiNER2 package resolution hint: {value}")
                elif entity.label == "repo_ref":
                    repository = _valid_repository_ref(entity.text)
                    if repository and repository_hint is None:
                        repository_hint = repository
                        resolution_source = "gliner2"
                        warnings.append(f"GLiNER2 repository resolution hint: {repository}")
        warnings.append(
            f"GLiNER2 query features: {features.intent or 'unclassified'} "
            f"({features.confidence:.2f})"
        )
        warnings.extend(features.warnings)
    if optimization is not None:
        for value in optimization.api_literals[:3]:
            if candidate := _valid_variant(value):
                variants.append((candidate, "lexical"))
        for value in [*optimization.identifiers[:4], *optimization.symbols[:3]]:
            if candidate := _valid_variant(value):
                variants.append((candidate, "symbol"))
        for value in optimization.exact_phrases[:2]:
            candidate = value.strip()
            if 3 <= len(candidate) <= 80 and candidate.casefold() not in _GENERIC:
                variants.append((candidate, "lexical"))
        warnings.append(f"worker_llm optimized objective: {optimization.objective}")
    warnings.extend(failures)

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value, kind in variants:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((value, kind))
        if value not in anchors and len(value) >= 3:
            anchors.append(value)
        if len(unique) >= 3:
            break
    return replace(
        plan,
        variants=tuple(value for value, _ in unique),
        variant_kinds=tuple(kind for _, kind in unique),
        anchor_terms=tuple(anchors),
        qualifiers=tuple(qualifiers),
        warnings=tuple(dict.fromkeys(warnings)),
        exa_semantic_query=(
            optimization.exa_semantic_query.strip()
            if optimization is not None and optimization.exa_semantic_query.strip()
            else plan.exa_semantic_query
        ),
        library_hint=library_hint,
        repository_hint=repository_hint,
        resolution_source=resolution_source,
    )


async def optimize_query_plan(plan: QueryPlan, request: CodeSearchRequest) -> QueryPlan:
    """Enrich an already valid deterministic plan; never make retrieval depend on models."""

    feature_task = asyncio.create_task(get_gliner_client().analyze_query_features(request.query))
    worker_task = (
        asyncio.create_task(_worker_optimization(request.query, request.research_goal))
        if _needs_worker(plan, request)
        else None
    )
    failures: list[str] = []
    features: QueryFeatureAnalysis | None = None
    optimization: CodeQueryOptimization | None = None
    try:
        features = await asyncio.wait_for(feature_task, timeout=3.0)
    except Exception as exc:
        failures.append(f"GLiNER2 optimization fallback: {type(exc).__name__}")
        if not feature_task.done():
            feature_task.cancel()
            await asyncio.gather(feature_task, return_exceptions=True)
    if worker_task is not None:
        try:
            optimization = await worker_task
        except Exception as exc:
            failures.append(f"worker_llm optimization fallback: {type(exc).__name__}")
    return _merge_enrichment(plan, features, optimization, failures)
