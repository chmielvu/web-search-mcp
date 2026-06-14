from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .snippet_normalizer import normalize_snippet

WEB_SEARCH_RESULT_FIELDS = (
    "title",
    "link",
    "snippet",
    "domain",
    "published_date",
    "providers",
    "provider_count",
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    value = _dump(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def serialize_public_web_search_result(result: Any) -> dict[str, Any]:
    raw = _as_dict(result)
    public: dict[str, Any] = {}
    for field in WEB_SEARCH_RESULT_FIELDS:
        if field in raw and raw[field] is not None:
            public[field] = raw[field]
    # Normalize snippet: strip HTML, base64, nav chrome, cap length
    if "snippet" in public:
        public["snippet"] = normalize_snippet(public["snippet"])
    return public


def serialize_public_web_search_response(response: Any) -> dict[str, Any]:
    raw = _as_dict(response)
    results = raw.get("results", []) or []
    public_results = [serialize_public_web_search_result(result) for result in results]

    public: dict[str, Any] = {
        "query": raw.get("query", ""),
        "results": public_results,
        "total_results": raw.get("total_results", len(public_results)),
        "providers_used": list(raw.get("providers_used") or []),
    }

    if "result_window" in raw and raw["result_window"] is not None:
        public["result_window"] = _dump(raw["result_window"])
    if "warnings" in raw and raw["warnings"] is not None:
        public["warnings"] = _dump(raw["warnings"])

    return public
