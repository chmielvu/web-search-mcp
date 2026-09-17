"""Shared rendering and serialization for provider testimony."""

from typing import TypedDict

from .types import AnswerKind, SearchHit, SourceKind


class SearchHitTestimony(TypedDict):
    """JSON-shaped native testimony stored on analytics and index payloads."""

    engine_rank: int | None
    provider_score: float | None
    source_name: str | None
    source_kind: SourceKind | None
    published: str | None
    highlights: list[str]
    source_engines: list[str]
    origin_adapters: list[str]
    answer_kind: AnswerKind | None


def evidence_passages(hit: SearchHit) -> tuple[str, ...]:
    """Return stable, de-duplicated page evidence without changing the public snippet."""
    passages: list[str] = []
    seen: set[str] = set()
    for value in (hit.snippet, *hit.highlights):
        normalized = " ".join(value.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        if passages and key in passages[0].casefold():
            continue
        seen.add(key)
        passages.append(normalized)
    return tuple(passages)


def render_search_hit_text(hit: SearchHit, *, max_chars: int) -> str:
    """Render title plus provider evidence for lexical and embedding stages."""
    title = " ".join(hit.title.split())
    if max_chars <= 0:
        return title
    parts = ([title] if title else []) + list(evidence_passages(hit))
    rendered = "\n".join(parts)
    return rendered[:max_chars].rstrip()


def testimony_payload(hit: SearchHit) -> SearchHitTestimony:
    """Serialize native testimony for analytics rows and the Qdrant payload."""
    return {
        "engine_rank": hit.engine_rank,
        "provider_score": hit.provider_score,
        "source_name": hit.source_name,
        "source_kind": hit.source_kind,
        "published": hit.published,
        "highlights": list(hit.highlights),
        "source_engines": list(hit.source_engines),
        "origin_adapters": list(hit.origin_adapters),
        "answer_kind": hit.answer_kind,
    }
