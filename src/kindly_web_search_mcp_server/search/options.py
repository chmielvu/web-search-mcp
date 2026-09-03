from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256

from ..search.normalize import normalize_query
from .filters import LocaleSpec, TemporalWindow


SEARCH_TIME_RANGES = frozenset({"day", "week", "month", "year"})


def _normalize_items(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    items: list[str] = []
    for raw in values:
        text = normalize_query(raw)
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class SearchOptions:
    searxng_categories: tuple[str, ...] = ()
    searxng_engines: tuple[str, ...] = ()
    searxng_language: str | None = None
    searxng_pageno: int = 1
    searxng_time_range: str | None = None
    searxng_safesearch: int | None = None
    temporal: TemporalWindow | None = None
    language: str | None = None
    region: str | None = None

    def validate(self) -> "SearchOptions":
        if self.searxng_pageno < 1:
            raise ValueError("searxng_pageno must be >= 1.")
        if self.searxng_time_range and self.searxng_time_range not in SEARCH_TIME_RANGES:
            raise ValueError(f"searxng_time_range must be one of {sorted(SEARCH_TIME_RANGES)}.")
        if self.searxng_safesearch is not None and self.searxng_safesearch not in {
            0,
            1,
            2,
        }:
            raise ValueError("searxng_safesearch must be 0, 1, 2, or None.")
        if self.temporal is not None and self.temporal.bucket is None and self.temporal.is_empty:
            raise ValueError("temporal window resolved empty; pass None instead.")
        return self

    def cache_fingerprint(self) -> str:
        payload = {
            "searxng_categories": self.searxng_categories,
            "searxng_engines": self.searxng_engines,
            "searxng_language": self.searxng_language,
            "searxng_pageno": self.searxng_pageno,
            "searxng_time_range": self.searxng_time_range,
            "searxng_safesearch": self.searxng_safesearch,
            "temporal": (
                {
                    "start": self.temporal.start.isoformat() if self.temporal.start else None,
                    "end": self.temporal.end.isoformat() if self.temporal.end else None,
                    "bucket": self.temporal.bucket,
                }
                if self.temporal is not None
                else None
            ),
            "language": self.language,
            "region": self.region,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

def build_search_options(
    *,
    searxng_categories: list[str] | None = None,
    searxng_engines: list[str] | None = None,
    searxng_language: str | None = None,
    searxng_pageno: int = 1,
    searxng_time_range: str | None = None,
    searxng_safesearch: int | None = None,
    date_range: str | None = None,
    after_date: str | None = None,
    before_date: str | None = None,
    language: str | None = None,
    region: str | None = None,
    gl: str | None = None,
    locale_spec: LocaleSpec | None = None,
    temporal_window: TemporalWindow | None = None,
) -> SearchOptions:
    options = SearchOptions(
        searxng_categories=_normalize_items(searxng_categories),
        searxng_engines=_normalize_items(searxng_engines),
        searxng_language=normalize_query(searxng_language) or None if searxng_language else None,
        searxng_pageno=searxng_pageno,
        searxng_time_range=(
            normalize_query(searxng_time_range).casefold() or None if searxng_time_range else None
        ),
        searxng_safesearch=searxng_safesearch,
        temporal=temporal_window,
        language=(locale_spec.language if locale_spec else None),
        region=(locale_spec.region if locale_spec else None),
    )
    return options.validate()
def build_search_identity_key(
    providers: list[str] | None,
    search_options: SearchOptions | None,
) -> str:
    """Return a stable identity key for a search request (no cache)."""
    provider_key = ",".join(sorted(providers or []))
    if search_options is None:
        return provider_key
    fingerprint = search_options.cache_fingerprint()
    return f"{provider_key}|{fingerprint}"
