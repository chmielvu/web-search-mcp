from __future__ import annotations

from typing import Any

from .observability_ids import _candidate_id, _canonical_result_id, _field
from .observability_tables import (
    ensure_pipeline_observability_tables,
    insert_web_search_response_results,
)


def build_response_result_rows(
    *,
    tool_call_id: str,
    run_key: str | None,
    cache_hit: str,
    results: list[Any],
    db_path: str | None = None,
) -> None:
    ensure_pipeline_observability_tables(db_path=db_path)
    for index, result in enumerate(results, start=1):
        title = str(_field(result, "title", "") or "")
        link = str(_field(result, "link", "") or "")
        snippet = str(_field(result, "snippet", "") or "")
        domain = _field(result, "domain", None)
        providers = list(_field(result, "providers", []) or [])
        provider_count = _field(result, "provider_count", None)
        score = _field(result, "score", None)
        insert_web_search_response_results(
            db_path=db_path,
            tool_call_id=tool_call_id,
            run_key=run_key,
            cache_hit=cache_hit,
            result_rank=index,
            title=title,
            link=link,
            snippet=snippet,
            domain=domain,
            providers=providers,
            provider_count=provider_count,
            score=score,
            candidate_id=_candidate_id(link, title, snippet),
            canonical_result_id=_canonical_result_id(link),
            payload_json={
                "providers": providers,
                "provider_count": provider_count,
                "score": score,
                "title_len": len(title),
                "snippet_len": len(snippet),
                "link_hash": _canonical_result_id(link),
            },
        )
