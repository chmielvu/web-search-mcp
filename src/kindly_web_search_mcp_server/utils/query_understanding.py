"""Deterministic query-understanding fallback.

Pure-python fallback used only when the hosted GLiNER2 gateway fails or is
disabled. Never imports GLiNER, torch, or pydantic models.

Intent resolution (H9 fix — keyword sets removed):
  1. comparison markers (regex, precision-first; ``vs code`` is a product)
  2. ``general`` (abstention)

``classify_intent_by_embedding`` (2026-09-09) was removed: zero callers in
src/ — exported but never wired into the fallback resolver. Restore from
git history if an embedding kNN tier is ever needed again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..search.intents import SearchIntent

__all__ = [
    "FallbackUnderstanding",
    "TIME_CURRENT",
    "TIME_HISTORICAL",
    "TIME_RECENT",
    "resolve_fallback_understanding",
]

TimeSensitivity = Literal["none", "recent", "current", "historical"]

# --- S1: candidate markers (time terms are shared with search/understanding/adapter.py) ---

# Comparison markers (precision-first). ``vs code`` is a product: the guard
# requires the token boundary to close the word (``vs code-first`` is a real
# comparison; verified FP: the old \b matched through the hyphen).
_COMPARISON_SPLIT = re.compile(r"\b(?:vs\.?|versus|compared\s+(?:to|with))\b", re.I)
_COMPARISON_WORD = re.compile(r"\b(?:compare|comparison|comparing|versus|compared)\b", re.I)
_COMPARISON_VERB_PREFIX = re.compile(r"^(?:compare|comparison|comparing|compared)\b\s*", re.I)
_PRODUCT_VS_CODE = re.compile(r"\bvs\.?\s*code\b(?![\w-])", re.I)

# Time-sensitivity markers. Recent-interval forms ("past week", "last month")
# MUST win over bare "past" (verified mis-bucket: "python releases past week"
# -> historical because TIME_HISTORICAL ran first). Order here is load-bearing
# for _time_sensitivity precedence (current > recent > historical).
TIME_CURRENT = re.compile(r"\b(?:current|currently|now|today|latest)\b", re.I)
TIME_RECENT = re.compile(
    r"\b(?:recent|recently|(?:this|past|last)\s+(?:day|week|month|year)s?)\b", re.I
)
TIME_HISTORICAL = re.compile(r"\b(?:historical|history|formerly|deprecated)\b", re.I)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")

_STOP_SIDES = frozenset({"the", "and", "for", "with", "vs", "or", "to", "a", "an", "of", "in"})

_MAX_COMPARED = 3
_MAX_SPLIT_SCANS = 3


@dataclass(frozen=True, slots=True)
class FallbackUnderstanding:
    """Pure extraction result; callers map it onto ``QueryUnderstandingResult``.

    Fallback confidence semantics stay 0.0 with ``fallback=True`` upstream.
    """

    intent: SearchIntent
    compared_entities: tuple[str, ...]
    compared_spans: tuple[tuple[int, int], ...]
    time_sensitivity: TimeSensitivity
    should_decompose: bool
    preserved_terms: tuple[str, ...]
    rationale: str
    rules: tuple[str, ...] = ()


def _side_has_content(side: str) -> bool:
    words = _TOKEN.findall(side)
    return any(len(w) >= 2 and w.casefold() not in _STOP_SIDES for w in words)


def _extract_compared(
    text: str,
) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    """S1 candidates, S2 validate, S3 resolve, S4 normalize (dedupe/cap)."""
    if _PRODUCT_VS_CODE.search(text):
        return (), ()
    entities: list[str] = []
    spans: list[tuple[int, int]] = []
    scans = 0
    for marker in _COMPARISON_SPLIT.finditer(text):
        scans += 1
        if scans > _MAX_SPLIT_SCANS:
            break
        left_raw = text[: marker.start()]
        right_raw = text[marker.end() :]
        left = left_raw.rstrip()
        right = right_raw.lstrip()
        if not _side_has_content(left) or not _side_has_content(right):
            continue
        # A leading comparison verb is stripped so "compare X vs Y" yields "X"
        # (never "compare X") as the left entity; left span start advances by
        # the strip width. The right side lstrip advances its start the same way.
        left_clean = _COMPARISON_VERB_PREFIX.sub("", left)
        if not left_clean:
            continue
        left_delta = len(left) - len(left_clean)
        entities = [left_clean, right]
        spans = [
            (left_delta, len(left)),
            (marker.end() + (len(right_raw) - len(right)), len(text)),
        ]
        break
    if not entities and _COMPARISON_WORD.search(text) and " and " in text:
        parts = [p.strip() for p in text.split(" and ", 1)]
        if len(parts) == 2:
            # Strip a leading comparison verb so "compare fastapi and starlette"
            # yields ("fastapi", "starlette"), never ("compare fastapi", "starlette").
            clean: list[str] = []
            for part in parts:
                stripped = _COMPARISON_VERB_PREFIX.sub("", part)
                if not stripped:
                    break
                clean.append(stripped)
            if len(clean) == 2 and all(_side_has_content(p) for p in clean):
                entities = []
                spans = []
                cursor = 0
                for part in clean:
                    idx = text.find(part, cursor)
                    entities.append(part)
                    spans.append((idx, idx + len(part)))
                    cursor = idx + len(part)
    deduped: list[str] = []
    deduped_spans: list[tuple[int, int]] = []
    for surface, span in zip(entities, spans):
        key = surface.casefold()
        if key not in {d.casefold() for d in deduped}:
            deduped.append(surface)
            deduped_spans.append(span)
    return tuple(deduped[:_MAX_COMPARED]), tuple(deduped_spans[:_MAX_COMPARED])


def _coarse_intent(text: str, compared: tuple[str, ...] = ()) -> SearchIntent:
    """S2/S3: product exclusion beats markers; otherwise abstain to general.

    Bare ``vs`` alone is NOT comparison (precision-first abstention): require
    an explicit comparison word OR a structurally valid two-sided split. The
    extracted pair is reused from the single extraction pass (never re-scan).
    """
    if not _PRODUCT_VS_CODE.search(text):
        if _COMPARISON_WORD.search(text):
            return "comparison"
        if compared:
            return "comparison"
    return "general"


def _time_sensitivity(text: str) -> TimeSensitivity:
    """S3 precedence: current > recent > historical (mirrors adapter._derive_fields)."""
    if TIME_CURRENT.search(text):
        return "current"
    if TIME_RECENT.search(text):
        return "recent"
    if TIME_HISTORICAL.search(text):
        return "historical"
    return "none"


def resolve_fallback_understanding(
    query: str,
    *,
    intent_override: SearchIntent | None = None,
) -> FallbackUnderstanding:
    """Deterministic query understanding for the GLiNER outage/disabled path.

    Precision-first: ambiguous queries stay ``general`` (abstention) rather
    than being force-labeled. Deterministic and auditable — every decision is
    recorded in ``rules`` for telemetry (``query_understanding_events``).
    ``intent_override`` is applied after the deterministic coarse pass, when
    provided (extension point; currently no in-repo caller passes it).

    ``compared_spans`` are offsets into the ORIGINAL query (leading whitespace
    included) so callers can always reproduce ``query[start:end] == surface``.
    """
    raw = query or ""
    text = raw.strip()
    lead = len(raw) - len(raw.lstrip())
    compared, compared_spans = _extract_compared(text)  # single marker scan
    if lead and compared_spans:
        compared_spans = tuple((start + lead, end + lead) for start, end in compared_spans)
    intent = intent_override or _coarse_intent(text, compared)  # override wins
    rules: list[str] = []
    if intent == "comparison":
        rules.append("intent.comparison_marker")
    elif intent_override is not None and intent != "general":
        rules.append(f"intent.override:{intent}")
    if compared:
        rules.append("compared.split_marker")
    time_sensitivity = _time_sensitivity(text)
    if time_sensitivity != "none":
        rules.append(f"time.{time_sensitivity}")
    should_decompose = intent == "comparison" and len(compared) >= 2
    if should_decompose:
        rules.append("decompose.comparison_facets")
    rationale = "deterministic fallback" + (f"; {'; '.join(rules)}" if rules else "; general")
    return FallbackUnderstanding(
        intent=intent,
        compared_entities=compared,
        compared_spans=compared_spans,
        time_sensitivity=time_sensitivity,
        should_decompose=should_decompose,
        preserved_terms=(),
        rationale=rationale,
        rules=tuple(rules),
    )
