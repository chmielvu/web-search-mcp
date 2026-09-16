"""Universal search-filter translation into per-provider arguments.

The CLI accepts a small, provider-agnostic set of filters:
    --time / -t      day | week | month | year | YYYY-MM-DD..YYYY-MM-DD
    --lang / -l      ISO 639-1 (en, zh, ja, ...)
    --region / -r    ISO 3166 (US, CN, JP, ...)
    --site           repeatable (e.g. arxiv.org, *.edu)
    --exclude        repeatable

This module turns those into ``extra`` kwargs that each provider's ``_search``
already understands (or new ones we plumb in).  Providers silently ignore
filters they cannot honour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


_TIME_WORDS = {"day", "week", "month", "year"}
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")


@dataclass
class Filters:
    """Normalized filter container."""

    time: str | None = None  # "day"|"week"|"month"|"year" or "YYYY-MM-DD..YYYY-MM-DD"
    lang: str | None = None  # ISO 639-1, lower-case
    region: str | None = None  # ISO 3166, upper-case
    sites: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_cli(
        cls,
        time: str | None = None,
        lang: str | None = None,
        region: str | None = None,
        sites: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> "Filters":
        t = (time or "").strip().lower() or None
        if t and t not in _TIME_WORDS and not _DATE_RE.match(t):
            raise ValueError(
                f"--time must be one of day|week|month|year or YYYY-MM-DD..YYYY-MM-DD (got {time!r})"
            )
        return cls(
            time=t,
            lang=(lang or "").strip().lower() or None,
            region=(region or "").strip().upper() or None,
            sites=[s.strip() for s in (sites or []) if s and s.strip()],
            exclude=[s.strip() for s in (exclude or []) if s and s.strip()],
        )

    # ---- helpers ------------------------------------------------------------
    def time_range_dates(self) -> tuple[str, str] | None:
        """Expand --time to (start, end) ISO dates. Returns None only if no time filter set."""
        if not self.time:
            return None
        if self.time in _TIME_WORDS:
            today = date.today()
            spans = {
                "day": timedelta(days=1),
                "week": timedelta(days=7),
                "month": timedelta(days=30),
                "year": timedelta(days=365),
            }
            start = today - spans[self.time]
            return start.isoformat(), today.isoformat()
        m = _DATE_RE.match(self.time)
        if m:
            return m.group(1), m.group(2)
        return None

    def has_any(self) -> bool:
        return any([self.time, self.lang, self.region, self.sites, self.exclude])


# ---- per-provider translators ------------------------------------------------


def _site_query_suffix(query: str, sites: list[str], exclude: list[str]) -> str:
    """Append site:/-site: operators for engines that use them (Brave/Serper/Firecrawl/Jina)."""
    extras: list[str] = []
    if sites:
        if len(sites) == 1:
            extras.append(f"site:{sites[0]}")
        else:
            extras.append("(" + " OR ".join(f"site:{s}" for s in sites) + ")")
    for ex in exclude:
        extras.append(f"-site:{ex}")
    if extras:
        return query.strip() + " " + " ".join(extras)
    return query


def apply_to_brave(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    if f.time in {"day", "week", "month", "year"}:
        out["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[f.time]
    elif f.time and _DATE_RE.match(f.time):
        # Brave accepts YYYY-MM-DDtoYYYY-MM-DD as freshness param.
        s, e = f.time.split("..")
        out["freshness"] = f"{s}to{e}"
    if f.lang:
        out["search_lang"] = f.lang
    if f.region:
        out["country"] = f.region
    return _site_query_suffix(query, f.sites, f.exclude), out


def apply_to_serper(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    if f.time in {"day", "week", "month", "year"}:
        out["tbs"] = "qdr:" + {"day": "d", "week": "w", "month": "m", "year": "y"}[f.time]
    elif f.time and _DATE_RE.match(f.time):
        s, e = f.time.split("..")
        # Google's custom-date-range tbs format.
        s2, e2 = s.replace("-", "/"), e.replace("-", "/")
        out["tbs"] = f"cdr:1,cd_min:{s2},cd_max:{e2}"
    if f.lang:
        out["locale"] = f.lang
    if f.region:
        out["country"] = f.region
    return _site_query_suffix(query, f.sites, f.exclude), out


def apply_to_exa(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    rng = f.time_range_dates()
    if rng:
        out["start_published_date"] = rng[0] + "T00:00:00.000Z"
        out["end_published_date"] = rng[1] + "T23:59:59.999Z"
    if f.sites:
        out["include_domains"] = [s.lstrip("*.") for s in f.sites]
    if f.exclude:
        out["exclude_domains"] = [s.lstrip("*.") for s in f.exclude]
    if f.region:
        out["user_location"] = f.region
    # Exa has no native language filter — silently ignored.
    return query, out


def apply_to_tavily(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    if f.time in {"day", "week", "month", "year"}:
        out["time_range"] = f.time
    elif f.time and _DATE_RE.match(f.time):
        out["start_date"], out["end_date"] = f.time.split("..")
    if f.region:
        out["country"] = _tavily_country(f.region)
    if f.sites:
        out["include_domains"] = [s.lstrip("*.") for s in f.sites]
    if f.exclude:
        out["exclude_domains"] = [s.lstrip("*.") for s in f.exclude]
    return query, out


def apply_to_firecrawl(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    if f.time in {"day", "week", "month", "year"}:
        out["tbs"] = "qdr:" + {"day": "d", "week": "w", "month": "m", "year": "y"}[f.time]
    if f.region:
        out["country"] = f.region
    if f.lang:
        out["lang"] = f.lang  # provider puts this in scrapeOptions.location.languages
    if f.sites and not f.exclude:
        out["include_domains"] = [s.lstrip("*.") for s in f.sites]
        return query, out
    if f.exclude and not f.sites:
        out["exclude_domains"] = [s.lstrip("*.") for s in f.exclude]
        return query, out
    return _site_query_suffix(query, f.sites, f.exclude), out


def apply_to_jina(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    # Jina doesn't have rich filters; just append operators and pass-through lang as a header hint.
    if f.lang:
        out["locale"] = f.lang
    return _site_query_suffix(query, f.sites, f.exclude), out


_TAVILY_COUNTRIES = {
    "AU": "australia",
    "BR": "brazil",
    "CA": "canada",
    "CN": "china",
    "DE": "germany",
    "ES": "spain",
    "FR": "france",
    "GB": "united kingdom",
    "IN": "india",
    "IT": "italy",
    "JP": "japan",
    "KR": "south korea",
    "MX": "mexico",
    "NL": "netherlands",
    "SG": "singapore",
    "US": "united states",
}


def _tavily_country(region: str) -> str:
    return _TAVILY_COUNTRIES.get(region.upper(), region.lower())


def apply_to_searxng(query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out = dict(extra)
    if f.time in {"day", "week", "month", "year"}:
        out["time_range"] = f.time
    if f.lang:
        out["language"] = f.lang
    return _site_query_suffix(query, f.sites, f.exclude), out


_TRANSLATORS = {
    "brave": apply_to_brave,
    "serper": apply_to_serper,
    "exa": apply_to_exa,
    "tavily": apply_to_tavily,
    "firecrawl": apply_to_firecrawl,
    "jina": apply_to_jina,
    "searxng": apply_to_searxng,
}


def apply(provider: str, query: str, f: Filters, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate filters for the given provider, returning (effective_query, extra_kwargs)."""
    fn = _TRANSLATORS.get(provider)
    if fn is None:
        return query, dict(extra)
    return fn(query, f, extra)
