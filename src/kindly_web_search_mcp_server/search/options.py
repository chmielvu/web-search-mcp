from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from urllib.parse import urlparse

from ..search.normalize import normalize_query
from ..cache.query_cache import provider_cache_key


SEARCH_TIME_RANGES = frozenset({"day", "week", "month", "year"})


def _normalize_items(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    items: list[str] = []
    for raw in values:
        text = normalize_query(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return tuple(items)


def _normalize_site_filter(value: str) -> str:
    text = normalize_query(value)
    if not text:
        raise ValueError("site/domain filters cannot be empty.")
    if text.startswith(("site:", "domain:")):
        return text
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"site:{parsed.netloc.lower()}"
    if "/" in text:
        host = text.split("/", 1)[0].strip().lower()
        if host:
            return f"site:{host}"
    return f"site:{text.lower()}"


@dataclass(frozen=True, slots=True)
class SearchOptions:
    result_offset: int = 0
    searxng_categories: tuple[str, ...] = ()
    searxng_engines: tuple[str, ...] = ()
    searxng_language: str | None = None
    searxng_pageno: int = 1
    searxng_time_range: str | None = None
    searxng_safesearch: int | None = None
    site_filters: tuple[str, ...] = ()
    domain_filters: tuple[str, ...] = ()

    def validate(self) -> "SearchOptions":
        if self.result_offset < 0:
            raise ValueError("result_offset must be >= 0.")
        if self.searxng_pageno < 1:
            raise ValueError("searxng_pageno must be >= 1.")
        if (
            self.searxng_time_range
            and self.searxng_time_range not in SEARCH_TIME_RANGES
        ):
            raise ValueError(
                f"searxng_time_range must be one of {sorted(SEARCH_TIME_RANGES)}."
            )
        if self.searxng_safesearch is not None and self.searxng_safesearch not in {
            0,
            1,
            2,
        }:
            raise ValueError("searxng_safesearch must be 0, 1, 2, or None.")
        return self

    def cache_fingerprint(self) -> str:
        payload = {
            "result_offset": self.result_offset,
            "searxng_categories": self.searxng_categories,
            "searxng_engines": self.searxng_engines,
            "searxng_language": self.searxng_language,
            "searxng_pageno": self.searxng_pageno,
            "searxng_time_range": self.searxng_time_range,
            "searxng_safesearch": self.searxng_safesearch,
            "site_filters": self.site_filters,
            "domain_filters": self.domain_filters,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def query_suffix(self) -> str:
        terms = [_normalize_site_filter(value) for value in self.site_filters]
        terms.extend(_normalize_site_filter(value) for value in self.domain_filters)
        return " ".join(terms)


def build_search_options(
    *,
    result_offset: int = 0,
    searxng_categories: list[str] | None = None,
    searxng_engines: list[str] | None = None,
    searxng_language: str | None = None,
    searxng_pageno: int = 1,
    searxng_time_range: str | None = None,
    searxng_safesearch: int | None = None,
    site_filters: list[str] | None = None,
    domain_filters: list[str] | None = None,
) -> SearchOptions:
    options = SearchOptions(
        result_offset=result_offset,
        searxng_categories=_normalize_items(searxng_categories),
        searxng_engines=_normalize_items(searxng_engines),
        searxng_language=normalize_query(searxng_language) or None
        if searxng_language
        else None,
        searxng_pageno=searxng_pageno,
        searxng_time_range=(
            normalize_query(searxng_time_range).casefold() or None
            if searxng_time_range
            else None
        ),
        searxng_safesearch=searxng_safesearch,
        site_filters=_normalize_items(site_filters),
        domain_filters=_normalize_items(domain_filters),
    )
    return options.validate()


def build_search_identity_key(
    providers: list[str] | None,
    search_options: SearchOptions | None,
) -> str:
    provider_key = provider_cache_key(providers)
    if search_options is None:
        return provider_key
    fingerprint = search_options.cache_fingerprint()
    return f"{provider_key}|{fingerprint}"


def build_search_query(query: str, search_options: SearchOptions | None) -> str:
    normalized = normalize_query(query)
    if search_options is None:
        return normalized
    suffix = search_options.query_suffix()
    if not suffix:
        return normalized
    return normalize_query(f"{normalized} {suffix}")
