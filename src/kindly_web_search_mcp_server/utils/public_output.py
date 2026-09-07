"""Public web_search envelope and leftover-overflow cursor codec."""

from __future__ import annotations

import base64
import json
from typing import Any, Literal

from ..models import (
    ProviderWarning,
    WebSearchHit,
    WebSearchNext,
    WebSearchOverflowHit,
    WebSearchPublicResponse,
    WebSearchResult,
)
from ..rerank.limits import FINAL_RESULT_LIMIT
from ..search.contracts import BranchRole, SearchRun
from ..search.ranking import _build_freshness_signal
from .snippet_normalizer import normalize_snippet

_OVERFLOW_CURSOR_VERSION = 1

_PAGE1_FETCH_WHY = (
    "Evaluate these results first. Then call fetch on the URLs you need; "
    "snippets are not page text."
)
_OVERFLOW_FETCH_WHY = (
    "These leftover links were ranked below the top 15. Fetch only if you still need more sources."
)


def encode_web_search_overflow_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_web_search_overflow_cursor(cursor: str) -> dict[str, Any]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except Exception as exc:
        raise ValueError("Invalid web_search cursor") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Invalid web_search cursor")
    if decoded.get("v") != _OVERFLOW_CURSOR_VERSION or decoded.get("kind") != "web_search_overflow":
        raise ValueError("Unsupported web_search cursor version")
    if "items" not in decoded or not isinstance(decoded["items"], list):
        raise ValueError("Invalid web_search cursor")
    return decoded


def to_public_hit(result: WebSearchResult, citation_id: str) -> WebSearchHit:
    providers = result.providers
    return WebSearchHit(
        citation_id=citation_id,
        title=result.title,
        url=result.link,
        snippet=normalize_snippet(result.snippet),
        domain=result.domain,
        published_date=result.published_date,
        freshness=_build_freshness_signal(result.published_date),
        score=result.final_score if result.final_score is not None else None,
        consensus=len(providers) if providers is not None else None,
        providers=providers if providers else None,
    )


def _query_variants_from_plan(plan: Any) -> dict[str, str] | None:
    if plan is None:
        return None
    by_role = {branch.role: branch.query for branch in plan.branches}
    variants: dict[str, str] = {}
    for role in BranchRole:
        query = by_role.get(role)
        if query is not None:
            variants[role.value] = query
    return variants or None


def _compact_overflow(
    overflow_items: list[tuple[str, WebSearchResult]],
) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for stage, item in overflow_items:
        if not item.link:
            continue
        compact.append({"title": item.title, "url": item.link, "stage": stage})
    return compact


def to_public_web_search(
    *,
    query: str,
    intent: str | None,
    query_variants: dict[str, str] | None,
    hits: list[WebSearchResult],
    overflow_items: list[tuple[str, WebSearchResult]],
    warnings: list[ProviderWarning] | None,
) -> WebSearchPublicResponse:
    public_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, result in enumerate(hits, start=1):
        if not result.link:
            continue
        public_hits.append(to_public_hit(result, f"c{index}"))

    status: Literal["ok", "empty", "partial"]
    if not public_hits:
        status = "empty"
    elif warnings:
        status = "partial"
    else:
        status = "ok"

    next_items: list[WebSearchNext] | None = None
    if public_hits:
        next_items = [
            WebSearchNext(
                action="fetch",
                tool="fetch",
                query={"urls": [hit.url for hit in public_hits]},
                why=_PAGE1_FETCH_WHY,
                confidence="high",
            )
        ]

    overflow = _compact_overflow(overflow_items)
    cursor: str | None = None
    has_more: bool | None = None
    remaining: int | None = None
    if overflow:
        has_more = True
        remaining = len(overflow)
        cursor = encode_web_search_overflow_cursor(
            {
                "v": _OVERFLOW_CURSOR_VERSION,
                "kind": "web_search_overflow",
                "query": query,
                "intent": intent,
                "query_variants": query_variants,
                "citation_base": len(public_hits),
                "items": overflow,
                "warnings": [w.model_dump(exclude_none=True) for w in warnings] if warnings else [],
            }
        )

    return WebSearchPublicResponse(
        query=query,
        status=status,
        results=public_hits,
        intent=intent,
        query_variants=query_variants,
        warnings=warnings if warnings else None,
        next=next_items,
        has_more=has_more,
        remaining=remaining,
        cursor=cursor,
    )


def to_public_web_search_from_run(run: SearchRun) -> WebSearchPublicResponse:
    response = run.response
    if response is None:
        raise ValueError("Search run has no response")
    plan = run.plan
    intent = None
    if plan is not None and plan.understanding is not None:
        intent = str(plan.understanding.intent)
    return to_public_web_search(
        query=response.query,
        intent=intent,
        query_variants=_query_variants_from_plan(plan),
        hits=list(response.results),
        overflow_items=list(run.diagnostics.overflow_ranked),
        warnings=list(response.warnings) if response.warnings else None,
    )


def page_overflow_cursor(decoded: dict[str, Any]) -> WebSearchPublicResponse:
    items = decoded["items"]
    page = items[:FINAL_RESULT_LIMIT]
    rest = items[FINAL_RESULT_LIMIT:]
    if not page:
        raise ValueError("web_search cursor has no remaining results")
    citation_base = int(decoded.get("citation_base") or 0)
    overflow_hits: list[WebSearchHit | WebSearchOverflowHit] = []
    for index, item in enumerate(page, start=1):
        overflow_hits.append(
            WebSearchOverflowHit(
                citation_id=f"c{citation_base + index}",
                title=item["title"],
                url=item["url"],
                stage=item["stage"],
            )
        )
    query = str(decoded.get("query") or "")
    intent = decoded.get("intent")
    query_variants = decoded.get("query_variants")
    rebuilt_warnings = [
        ProviderWarning.model_validate(item)
        for item in (decoded.get("warnings") or [])
        if isinstance(item, dict)
    ]
    cursor = None
    has_more = None
    remaining = None
    if rest:
        has_more = True
        remaining = len(rest)
        cursor = encode_web_search_overflow_cursor(
            {
                "v": _OVERFLOW_CURSOR_VERSION,
                "kind": "web_search_overflow",
                "query": query,
                "intent": intent,
                "query_variants": query_variants,
                "citation_base": citation_base + len(page),
                "items": rest,
                "warnings": [w.model_dump(exclude_none=True) for w in rebuilt_warnings] if rebuilt_warnings else [],
            }
        )
    return WebSearchPublicResponse(
        query=query,
        status="partial" if rebuilt_warnings else "ok",
        results=overflow_hits,
        intent=intent if intent else None,
        query_variants=query_variants if query_variants else None,
        warnings=rebuilt_warnings or None,
        next=[
            WebSearchNext(
                action="fetch",
                tool="fetch",
                query={"urls": [hit.url for hit in overflow_hits]},
                why=_OVERFLOW_FETCH_WHY,
                confidence="medium",
            )
        ],
        has_more=has_more,
        remaining=remaining,
        cursor=cursor,
    )
