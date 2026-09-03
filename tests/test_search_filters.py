"""Tests for temporal/locale filter normalization and SearchOptions v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from kindly_web_search_mcp_server.search.filters import (
    FilterValidationError,
    TemporalWindow,
    brave_freshness,
    ddg_timelimit,
    filter_results_by_window,
    google_tbs_bucket,
    langsearch_freshness,
    normalize_locale,
    parse_published_date,
    resolve_window,
    searxng_time_range,
    should_drop_undated,
)
from kindly_web_search_mcp_server.search.options import build_search_options


TODAY = date(2026, 8, 24)


class TestResolveWindow:
    def test_relative_bucket_resolves_absolute_bounds(self) -> None:
        window = resolve_window(
            date_range="week", after_date=None, before_date=None, today=TODAY
        )
        assert window.bucket == "week"
        assert window.start == date(2026, 8, 17)
        assert window.end == TODAY
        assert not window.clamped_to_today

    def test_absolute_bounds_win_over_bucket(self) -> None:
        window = resolve_window(
            date_range="day",
            after_date="2026-01-01",
            before_date="2026-01-31",
            today=TODAY,
        )
        assert window.bucket is None
        assert window.start == date(2026, 1, 1)
        assert window.end == date(2026, 1, 31)

    def test_after_after_before_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            resolve_window(
                date_range=None,
                after_date="2026-02-01",
                before_date="2026-01-01",
                today=TODAY,
            )

    def test_future_end_clamps_to_today(self) -> None:
        window = resolve_window(
            date_range=None, after_date=None, before_date="2099-01-01", today=TODAY
        )
        assert window.end == TODAY
        assert window.clamped_to_today

    def test_future_start_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            resolve_window(
                date_range=None, after_date="2099-01-01", before_date=None, today=TODAY
            )

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            resolve_window(
                date_range=None, after_date="01/02/2026", before_date=None, today=TODAY
            )

    def test_invalid_bucket_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            resolve_window(
                date_range="fortnight", after_date=None, before_date=None, today=TODAY
            )


class TestNormalizeLocale:
    def test_gl_merges_into_region(self) -> None:
        spec = normalize_locale(language="PL", region=None, gl="pl")
        assert spec.language == "pl"
        assert spec.region == "PL"
        assert any("deprecated" in warning for warning in spec.warnings)

    def test_conflict_keeps_first_region(self) -> None:
        spec = normalize_locale(language=None, region="DE", gl="FR")
        assert spec.region == "DE"
        assert any("conflicts" in warning for warning in spec.warnings)

    def test_bad_language_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            normalize_locale(language="not-a-language", region=None, gl=None)

    def test_bad_region_raises(self) -> None:
        with pytest.raises(FilterValidationError):
            normalize_locale(language=None, region="POL", gl=None)


class TestWireTokenMappers:
    def test_brave_custom_range(self) -> None:
        window = resolve_window(
            date_range=None,
            after_date="2026-07-01",
            before_date="2026-07-31",
            today=TODAY,
        )
        assert brave_freshness(window) == "2026-07-01to2026-07-31"

    def test_brave_exact_bucket_token(self) -> None:
        window = resolve_window(date_range="month", after_date=None, before_date=None, today=TODAY)
        assert brave_freshness(window) == "pm"

    def test_google_tbs_supports_week(self) -> None:
        assert google_tbs_bucket("week") == "w"
        assert google_tbs_bucket("year") == "y"

    def test_searxng_lacks_week(self) -> None:
        assert searxng_time_range("week") is None
        assert searxng_time_range("month") == "month"

    def test_ddg_and_langsearch_buckets(self) -> None:
        assert ddg_timelimit("year") == "y"
        assert langsearch_freshness("week") == "oneWeek"



@dataclass
class _Row:
    published: str | None
    providers: list[str] | None = None


class TestPostFilter:
    def _window(self) -> TemporalWindow:
        return resolve_window(
            date_range=None,
            after_date="2026-08-01",
            before_date="2026-08-20",
            today=TODAY,
        )

    def _rows(self) -> list[_Row]:
        return [
            _Row(published="2026-07-15T10:00:00Z", providers=["serper"]),
            _Row(published="2026-08-10"),
            _Row(published=None, providers=["serper"]),
            _Row(published="garbage"),
            _Row(published="2026-08-25", providers=["tavily"]),
        ]

    def test_capability_default_drops_degraded_undated_only(self) -> None:
        kept, out_of_range, undated = filter_results_by_window(
            self._rows(),
            window=self._window(),
            get_published_date=lambda r: r.published,
            get_providers=lambda r: r.providers,
            include_undated=None,
        )
        assert out_of_range == 2
        assert undated == 1  # serper-only undated dropped; unattributed kept
        assert len(kept) == 2

    def test_keep_all_policy(self) -> None:
        kept, _, undated = filter_results_by_window(
            self._rows(),
            window=self._window(),
            get_published_date=lambda r: r.published,
            get_providers=lambda r: r.providers,
            include_undated=True,
        )
        assert undated == 0
        assert len(kept) == 3

    def test_drop_all_policy(self) -> None:
        kept, _, undated = filter_results_by_window(
            self._rows(),
            window=self._window(),
            get_published_date=lambda r: r.published,
            get_providers=lambda r: r.providers,
            include_undated=False,
        )
        assert undated == 2
        assert len(kept) == 1


class TestUndatedPolicy:
    def test_native_provider_undated_kept_by_default(self) -> None:
        assert not should_drop_undated(include_undated=None, providers=["tavily"])

    def test_degraded_provider_undated_dropped_by_default(self) -> None:
        assert should_drop_undated(include_undated=None, providers=["ddg"])

    def test_explicit_flags_win(self) -> None:
        assert not should_drop_undated(include_undated=True, providers=None)
        assert should_drop_undated(include_undated=False, providers=["tavily"])

class TestSearchOptionsFingerprint:
    def test_fingerprint_distinguishes_temporal_and_locale(self) -> None:
        base = build_search_options()
        filtered = build_search_options(
            temporal_window=resolve_window(
                date_range="day", after_date=None, before_date=None, today=TODAY
            ),
            language="pl",
            region="PL",
        )
        assert base.cache_fingerprint() != filtered.cache_fingerprint()

    def test_fingerprint_stable_for_same_inputs(self) -> None:
        a = build_search_options(language="en", region="US")
        b = build_search_options(language="en", region="US")
        assert a.cache_fingerprint() == b.cache_fingerprint()


def test_parse_published_date_variants() -> None:
    assert parse_published_date("2026-08-24T12:00:00Z") == date(2026, 8, 24)
    assert parse_published_date("2026-08-24") == date(2026, 8, 24)
    assert parse_published_date("nonsense") is None
    assert parse_published_date(None) is None
