"""Temporal and locale filter normalization shared by search adapters.

The public contract accepts relative buckets (``date_range``) and absolute
ISO-8601 date bounds (``after_date``/``before_date``) plus ``language`` and
``region``.  This module resolves them once, at request time, into an
immutable :class:`TemporalWindow` so every provider receives identical
semantics, and provides the verified per-provider wire-token mappers.

Provider capabilities are encoded from primary API documentation:

- Brave ``freshness``: pd/pw/pm/py or ``YYYY-MM-DDtoYYYY-MM-DD``.
- Tavily: ``start_date``/``end_date`` (YYYY-MM-DD) and relative
  ``time_range`` (day/week/month/year/d/w/m/y).
- SearXNG ``time_range``: day/month/year only (no week).
- Serper/Google-family ``tbs``: qdr:h/d/w/m/y.
- DuckDuckGo (ddgs): ``timelimit`` d/w/m/y.
- LangSearch (Bing-compatible): freshness oneDay/oneWeek/oneMonth/oneYear.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

TEMPORAL_BUCKETS: tuple[str, ...] = ("day", "week", "month", "year")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_REGION_RE = re.compile(r"^[A-Za-z]{2}$")

_BUCKET_DELTA_DAYS: dict[str, int] = {"day": 1, "week": 7, "month": 30, "year": 365}


class FilterValidationError(ValueError):
    """Raised when caller-supplied temporal/locale filters are invalid."""


@dataclass(frozen=True, slots=True)
class TemporalWindow:
    """Absolute publication window resolved from public filter inputs."""

    start: date | None = None
    end: date | None = None
    bucket: str | None = None
    clamped_to_today: bool = False

    @property
    def is_empty(self) -> bool:
        return self.start is None and self.end is None


@dataclass(frozen=True, slots=True)
class LocaleSpec:
    """Normalized language/region pair with any merge warnings."""

    language: str | None = None
    region: str | None = None
    warnings: tuple[str, ...] = ()


def _parse_iso_date(value: str, *, label: str) -> date:
    text = value.strip()
    if not _ISO_DATE_RE.match(text):
        raise FilterValidationError(f"{label} must be ISO YYYY-MM-DD, got {value!r}.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise FilterValidationError(f"{label} is not a valid date: {value!r}.") from exc


def resolve_window(
    *,
    date_range: str | None,
    after_date: str | None,
    before_date: str | None,
    today: date | None = None,
) -> TemporalWindow:
    """Resolve public temporal inputs into one absolute window.

    Absolute bounds win over the relative bucket; when both are supplied the
    bucket is ignored (the tool layer surfaces that as a parameter warning).
    A future ``before_date`` is clamped to today and flagged; a future
    ``after_date`` is rejected outright.
    """
    reference_today = today or date.today()

    start = _parse_iso_date(after_date, label="after_date") if after_date else None
    end = _parse_iso_date(before_date, label="before_date") if before_date else None

    if start and end and start > end:
        raise FilterValidationError(
            f"after_date ({start.isoformat()}) must not be after before_date ({end.isoformat()})."
        )

    clamped = False
    if end is not None and end > reference_today:
        end = reference_today
        clamped = True
    if start is not None and start > reference_today:
        raise FilterValidationError(f"after_date ({start.isoformat()}) lies in the future.")

    bucket: str | None = None
    if start is None and end is None and date_range:
        normalized = date_range.strip().casefold()
        if normalized not in TEMPORAL_BUCKETS:
            raise FilterValidationError(
                f"date_range must be one of {list(TEMPORAL_BUCKETS)}, got {date_range!r}."
            )
        bucket = normalized
        delta = timedelta(days=_BUCKET_DELTA_DAYS[bucket])
        start = reference_today - delta
        end = reference_today

    return TemporalWindow(start=start, end=end, bucket=bucket, clamped_to_today=clamped)


def normalize_locale(
    *,
    language: str | None,
    region: str | None,
    gl: str | None,
) -> LocaleSpec:
    """Validate and merge locale inputs; ``gl`` is a deprecated region alias."""
    warnings: list[str] = []

    normalized_language: str | None = None
    if language:
        candidate = language.strip().lower()
        if not _LANGUAGE_RE.match(candidate):
            raise FilterValidationError(
                f"language must be an ISO 639-1 code or BCP-47 tag, got {language!r}."
            )
        normalized_language = candidate

    normalized_region: str | None = None
    for source_label, raw_value in (("region", region), ("gl", gl)):
        if not raw_value:
            continue
        candidate = raw_value.strip().upper()
        if not _REGION_RE.match(candidate):
            raise FilterValidationError(
                f"{source_label} must be an ISO 3166-1 alpha-2 code, got {raw_value!r}."
            )
        if normalized_region and normalized_region != candidate:
            warnings.append(
                f"{source_label}={candidate} conflicts with earlier region="
                f"{normalized_region}; keeping {normalized_region}."
            )
            continue
        if source_label == "gl":
            warnings.append("gl is deprecated; prefer region.")
        normalized_region = normalized_region or candidate

    return LocaleSpec(
        language=normalized_language, region=normalized_region, warnings=tuple(warnings)
    )


# ---------------------------------------------------------------------------
# Shared wire-token mappers (verified against provider documentation).
# ---------------------------------------------------------------------------


def brave_freshness(window: TemporalWindow) -> str | None:
    """Brave ``freshness`` token: exact bucket when clean, else custom range."""
    if window.is_empty:
        return None
    if window.bucket and not window.clamped_to_today:
        return {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[window.bucket]
    if window.start and window.end:
        return f"{window.start.isoformat()}to{window.end.isoformat()}"
    return None


def google_tbs_bucket(bucket: str | None) -> str | None:
    """Google-family ``tbs=qdr:<x>`` for a relative bucket (supports week)."""
    mapping = {"day": "d", "week": "w", "month": "m", "year": "y"}
    if bucket is None:
        return None
    return mapping.get(bucket)


def ddg_timelimit(bucket: str | None) -> str | None:
    """DuckDuckGo ``timelimit`` token (supports all four buckets)."""
    mapping = {"day": "d", "week": "w", "month": "m", "year": "y"}
    if bucket is None:
        return None
    return mapping.get(bucket)


def searxng_time_range(bucket: str | None) -> str | None:
    """SearXNG ``time_range``; returns None for 'week' which it does not support."""
    if bucket in {"day", "month", "year"}:
        return bucket
    return None


def langsearch_freshness(bucket: str | None) -> str | None:
    """LangSearch ``freshness`` bucket tokens."""
    mapping = {"day": "oneDay", "week": "oneWeek", "month": "oneMonth", "year": "oneYear"}
    if bucket is None:
        return None
    return mapping.get(bucket)


def tavily_time_range(bucket: str | None) -> str | None:
    """Tavily relative ``time_range`` (accepts full words)."""
    return bucket


# Static capability annotation from the verified matrix; used to surface a
# degradation warning when a constrained provider participates in a filtered run.
PROVIDER_TEMPORAL_MODE: dict[str, str] = {
    "brave": "native",
    "tavily": "native",
    "exa": "native",
    "searxng": "native_partial",
    "serper": "relative_only",
    "ddg": "relative_only",
    "langsearch": "relative_only",
    "brightdata_google_news": "relative_only",
    "brightdata_bing": "none",
    "brightdata_yandex": "none",
    "degoog": "none",
    "serpapi": "none",
    "gemma": "none",
    "grok": "none",
    "telegram": "none",
}


def parse_published_date(value: object) -> date | None:
    """Lenient published-date parser used by the post-filter safety net.

    Understands ISO datetimes/timestamps and a leading YYYY-MM-DD prefix.
    Returns None for anything unparseable — undated results are never dropped.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    head = text[:10]
    if _ISO_DATE_RE.match(head):
        try:
            return date.fromisoformat(head)
        except ValueError:
            return None
    return None



def provider_temporal_mode(name: str) -> str:
    """Capability class for a provider name (see PROVIDER_TEMPORAL_MODE)."""
    return PROVIDER_TEMPORAL_MODE.get((name or "").strip().lower(), "unknown")


def should_drop_undated(
    *,
    include_undated: bool | None,
    providers: Sequence[str] | None,
) -> bool:
    """Decision rule for undated results under an absolute window.

    - ``include_undated=True``  → always keep (explicit opt-out of strictness).
    - ``include_undated=False`` → always drop (fully strict window).
    - ``None`` (default)        → capability-aware: drop only when every
      contributing provider was temporally degraded (no native absolute
      support), because those are the ones that can leak stale pages past
      the requested window. Undated results from native providers were
      already bounded server-side and stay.
    """
    if include_undated is True:
        return False
    if include_undated is False:
        return True
    names = [n for n in (providers or []) if n]
    if not names:
        # Unknown provenance under a strict-ish default: keep, but callers
        # record this as kept_unknown_provenance in stats.
        return False
    return all(provider_temporal_mode(n) not in {"native"} for n in names)

def filter_results_by_window[T](
    results: Sequence[T],
    *,
    window: TemporalWindow,
    get_published_date: Callable[[T], object],
    get_providers: Callable[[T], Sequence[str] | None] | None = None,
    include_undated: bool | None = None,
) -> tuple[list[T], int, int]:
    """Apply the absolute window; optionally drop undated results by policy.

    Returns ``(kept, dropped_out_of_range, dropped_undated)``. Undated items
    are only dropped when ``should_drop_undated`` says so for the item's
    contributing providers (see that function for the default policy).
    """
    if window.is_empty:
        return list(results), 0, 0
    kept: list[T] = []
    dropped_out_of_range = 0
    dropped_undated = 0
    for item in results:
        published = parse_published_date(get_published_date(item))
        if published is None:
            if should_drop_undated(
                include_undated=include_undated,
                providers=get_providers(item) if get_providers else None,
            ):
                dropped_undated += 1
                continue
            kept.append(item)
            continue
        if (window.start and published < window.start) or (
            window.end and published > window.end
        ):
            dropped_out_of_range += 1
            continue
        kept.append(item)
    return kept, dropped_out_of_range, dropped_undated
