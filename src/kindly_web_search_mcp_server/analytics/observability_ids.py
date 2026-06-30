from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def _stable_hash(value: Any, *, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return sha256(raw).hexdigest()[:length]


def _candidate_id(link: str | None, title: str | None, snippet: str | None) -> str:
    return _stable_hash(
        {
            "link": (link or "").strip().lower(),
            "title": (title or "").strip(),
            "snippet": (snippet or "").strip(),
        }
    )


def _canonical_result_id(link: str | None) -> str:
    return _stable_hash({"link": (link or "").strip().lower()})


def _field(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)

