from __future__ import annotations

import json
import logging
import os
from typing import Any


_DEFAULT_MAX_TEXT_CHARS = 20000
_DEFAULT_MAX_ITEMS = 10
_DEFAULT_PREVIEW_CHARS = 2000


def _get_int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _max_text_chars() -> int:
    return _get_int_env("KINDLY_OBSERVABILITY_MAX_TEXT_CHARS", _DEFAULT_MAX_TEXT_CHARS)


def _max_items() -> int:
    return _get_int_env("KINDLY_OBSERVABILITY_MAX_ITEMS", _DEFAULT_MAX_ITEMS)


def preview_text(value: str | None, *, limit: int | None = None) -> str:
    if not value:
        return ""
    hard_limit = limit or _get_int_env(
        "KINDLY_OBSERVABILITY_PREVIEW_CHARS", _DEFAULT_PREVIEW_CHARS
    )
    if len(value) <= hard_limit:
        return value
    return value[:hard_limit].rstrip() + "…"


def _normalize_for_body(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return preview_text(value, limit=_max_text_chars())
    if hasattr(value, "model_dump"):
        return _normalize_for_body(value.model_dump())
    if isinstance(value, dict):
        items = list(value.items())[: _max_items()]
        return {str(k): _normalize_for_body(v) for k, v in items}
    if isinstance(value, (list, tuple, set)):
        values = list(value)[: _max_items()]
        return [_normalize_for_body(item) for item in values]
    return preview_text(str(value), limit=_max_text_chars())


def _normalize_for_extra(value: Any) -> str | bool | int | float | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return preview_text(value, limit=_max_text_chars())
    return json.dumps(_normalize_for_body(value), ensure_ascii=True, sort_keys=True)


def _record_key(name: str) -> str:
    normalized = []
    for char in name:
        normalized.append(char if char.isalnum() else "_")
    key = "".join(normalized).strip("_")
    return f"kindly_{key}" if key else "kindly_field"


def serialize_search_results(results: list[Any], *, max_results: int | None = None) -> list[dict[str, Any]]:
    limit = max_results or _max_items()
    serialized: list[dict[str, Any]] = []
    for result in results[:limit]:
        serialized.append(
            {
                "title": preview_text(getattr(result, "title", ""), limit=1000),
                "link": getattr(result, "link", ""),
                "snippet": preview_text(getattr(result, "snippet", ""), limit=4000),
                "domain": getattr(result, "domain", ""),
                "providers": list(getattr(result, "providers", []) or []),
                "provider_count": getattr(result, "provider_count", None),
                "score": getattr(result, "score", None),
            }
        )
    return serialized


def emit_observability_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    payload = {"event": event}
    payload.update({name: _normalize_for_body(value) for name, value in fields.items()})

    extra = {"kindly_event": event}
    for name, value in fields.items():
        extra[_record_key(name)] = _normalize_for_extra(value)

    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        extra=extra,
    )
