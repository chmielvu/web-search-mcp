"""Query language identification via lingua-py (always-on).

Uses lingua's Rust-backed detector restricted to the languages this server
actually sees in traffic (README guidance: restricting the candidate set is
recommended for both accuracy and speed). High-accuracy mode is mandatory —
queries are short (<120 chars), where lingua's low-accuracy mode degrades.

The detector is built once at import (eager model preload avoids cold-path
latency spikes) and is thread-safe per upstream docs. Breaking/non-gated
design: no feature flag; missing dependency fails loudly at import.
"""
from __future__ import annotations

from lingua import Language, LanguageDetectorBuilder

_SUPPORTED = (
    Language.ENGLISH,
    Language.POLISH,
    Language.GERMAN,
    Language.SPANISH,
    Language.FRENCH,
)

# Reliability gate thresholds, calibrated on live probes: true detections sit
# at top >= 0.75 with margin >= 0.63; confidently-wrong short-query picks sit
# at margin <= 0.21. Values in between are treated as unknown.
MIN_CONFIDENCE = 0.70
MIN_MARGIN = 0.40

_detector = (
    LanguageDetectorBuilder.from_languages(*_SUPPORTED)
    .with_preloaded_language_models()
    .build()
)


def detect_lang(text: str) -> str:
    """Return ISO-639-1 code (lowercase) for the query language, '' if unknown.

    A detection is only trusted when the top language scores >= MIN_CONFIDENCE
    AND leads the runner-up by >= MIN_MARGIN. Short keyword-style English
    queries routinely produce confidently-wrong low-margin picks (e.g.
    'async context manager python' -> de at 0.43/0.13); those map to '' so
    callers treat them as unknown instead of acting on a guess.
    """
    if not text or not any(ch.isalpha() for ch in text):
        return ""
    values = _detector.compute_language_confidence_values(text)
    if not values:
        return ""
    top = values[0]
    second = values[1].value if len(values) > 1 else 0.0
    if top.value < MIN_CONFIDENCE or (top.value - second) < MIN_MARGIN:
        return ""
    return top.language.iso_code_639_1.name.lower()
