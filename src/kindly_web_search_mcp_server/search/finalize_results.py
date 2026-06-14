"""Post-process merged search results into the public response shape."""

from __future__ import annotations

import logging

from ..models import ProviderWarning, WebSearchResponse, WebSearchResult
from ..settings import settings
from ..utils.observability import emit_observability_event
from .provider_config import DiagnosisCategory, diagnose_providers

# Categories that produce user-visible warnings.
# "unconfigured" and "intent_excluded" are silent by design.
_WARNING_CATEGORIES = frozenset({
    DiagnosisCategory.cooldown,
    DiagnosisCategory.disabled,
})
from .query_policy import RewritePolicy
from .entity_extractor import extract_entities

logger = logging.getLogger(__name__)


async def maybe_extract_entities(
    *,
    query: str,
    results: list[WebSearchResult],
) -> list[WebSearchResult]:
    enabled = bool(getattr(settings, "entity_extraction_enabled", False))
    if not enabled or not results:
        return results

    try:
        updated_results: list[WebSearchResult] = []
        for result in results:
            text = (
                f"{getattr(result, 'title', '') or (result.get('title') if isinstance(result, dict) else '')} "
                f"{getattr(result, 'snippet', '') or (result.get('snippet') if isinstance(result, dict) else '')}"
            ).strip()
            if not text:
                updated_results.append(result)
                continue
            entities = await extract_entities(text)
            if isinstance(result, dict):
                result["entities"] = entities or None
                updated_results.append(result)
            elif hasattr(result, "model_copy"):
                try:
                    updated_results.append(
                        result.model_copy(update={"entities": entities or None})
                    )
                except Exception:
                    updated_results.append(result)
            else:
                updated_results.append(result)
        results = updated_results
        emit_observability_event(
            logger,
            "entity.search_result_extracted",
            query=query,
            num_results=len(results),
            total_entities=sum(
                len(getattr(result, "entities", []) or []) for result in results
            ),
        )
    except Exception as exc:
        emit_observability_event(
            logger,
            "entity.extraction.error",
            query=query,
            error=str(exc)[:300],
            failure_mode="search_result_extract_failed",
            component="orchestrator_entity",
        )
        logger.warning("Search result entity extraction failed: %s", exc)
    return results


def build_search_response(
    *,
    query: str,
    normalized_query: str,
    research_goal: str | None,
    rewrite: bool,
    rewrite_policy: RewritePolicy,
    unique_domains: int,
    merged: list[WebSearchResult],
    final_results: list[WebSearchResult],
    result_offset: int,
    candidate_count: int,
    has_more: bool,
    next_offset: int | None,
) -> tuple[list[ProviderWarning], list[str], WebSearchResponse]:
    provider_diagnoses = diagnose_providers()
    provider_warnings = [
        ProviderWarning(provider=d.name, error=d.reason, error_type=d.category.value)
        for d in provider_diagnoses
        if not d.available and d.category in _WARNING_CATEGORIES
    ]
    providers_used = sorted(set(p for r in final_results for p in (r.providers or [])))

    emit_observability_event(
        logger,
        "search.orchestrator.response",
        query=query,
        research_goal=research_goal,
        normalized_query=normalized_query,
        rewrite_enabled=rewrite,
        rewrite_policy=rewrite_policy.mode,
        rewrite_reason=rewrite_policy.reason,
        unique_domains=unique_domains,
        merged_result_count=len(merged),
        final_result_count=len(final_results),
        providers_used=providers_used,
        warnings=[warning.model_dump() for warning in provider_warnings],
        results=final_results,
        merged_results=merged,
        result_window={
            "offset": result_offset,
            "returned": len(final_results),
            "candidate_count": candidate_count,
            "has_more": has_more,
            "next_offset": next_offset,
        },
    )

    return (
        provider_warnings,
        providers_used,
        WebSearchResponse(
            query=query,
            results=final_results,
            total_results=len(final_results),
            result_window={
                "offset": result_offset,
                "returned": len(final_results),
                "candidate_count": candidate_count,
                "has_more": has_more,
                "next_offset": next_offset,
            },
            providers_used=providers_used,
            warnings=provider_warnings or None,
        ),
    )
