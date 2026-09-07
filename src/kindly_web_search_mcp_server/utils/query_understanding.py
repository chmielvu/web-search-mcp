"""Deterministic query-understanding fallback with optional embedding kNN.

Pure-python fallback used only when the hosted GLiNER2 gateway fails or is
disabled. Never imports GLiNER, torch, or pydantic models.

Intent resolution (H9 fix — keyword sets removed):
  1. comparison markers (regex, precision-first; ``vs code`` is a product)
  2. embedding kNN over per-intent prototype exemplars (abstains on low
     margin or embedding errors — curia ``EmbeddingQueryRouter`` pattern)
  3. ``general`` (abstention)
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Literal

from ..search.intents import SearchIntent

__all__ = [
    "FallbackUnderstanding",
    "TIME_CURRENT",
    "TIME_HISTORICAL",
    "TIME_RECENT",
    "classify_intent_by_embedding",
    "resolve_fallback_understanding",
]

TimeSensitivity = Literal["none", "recent", "current", "historical"]

# --- S1: candidate markers (time terms are shared with search/understanding/adapter.py) ---

_COMPARISON_SPLIT = re.compile(r"\b(?:vs\.?|versus|compared\s+(?:to|with))\b", re.I)
_COMPARISON_WORD = re.compile(r"\b(?:compare|comparison|comparing|versus|compared)\b", re.I)
_COMPARISON_VERB_PREFIX = re.compile(r"^(?:compare|comparison|comparing|compared)\b\s*", re.I)
_PRODUCT_VS_CODE = re.compile(r"\bvs\s*code\b", re.I)

TIME_CURRENT = re.compile(r"\b(?:current|currently|now|today|latest)\b", re.I)
TIME_RECENT = re.compile(r"\b(?:recent|recently|this\s+week|this\s+month)\b", re.I)
TIME_HISTORICAL = re.compile(r"\b(?:historical|history|formerly|deprecated|past)\b", re.I)

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


# --- Embedding kNN intent tier (curia EmbeddingQueryRouter pattern) ---

_INTENT_EXEMPLARS: dict[SearchIntent, tuple[str, ...]] = {
    "ai_coding_and_infrastructure": (
        "how to fix async timeout error in fastapi",
        "python library for parsing html",
        "docker kubernetes deployment best practices",
        "sqlalchemy session connection pool",
    ),
    "social_media": (
        "twitter thread about the new model release",
        "best subreddits for mechanical keyboards",
        "instagram reel ideas for coffee shops",
        "facebook group for local hiking",
    ),
    "news": (
        "latest headlines about the election",
        "breaking news on the merger",
        "policy announcement this week",
        "launch event coverage today",
    ),
    "general": (
        "history of the printing press",
        "how do solar panels work",
        "best recipes for sourdough bread",
        "overview of roman aqueducts",
    ),
}

_INTENT_PROTOTYPES: dict[SearchIntent, list[float]] | None = None
_EMBED_MARGIN = 0.15


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


async def classify_intent_by_embedding(
    text: str,
    embed,
    *,
    margin: float = _EMBED_MARGIN,
) -> SearchIntent | None:
    """Nearest-prototype intent classification; abstains (``None``) on low margin.

    ``embed`` is any async callable ``str -> list[float]`` (repo convention:
    ``embeddings.embed_query`` / ``ml.embed_query``). Prototype vectors are
    built once from ``_INTENT_EXEMPLARS`` and cached. Abstains when the
    top-2 margin is below ``margin`` or the embedder errors — callers keep
    the deterministic ``general`` fallback.
    """
    global _INTENT_PROTOTYPES
    if not text or not text.strip():
        return None
    try:
        if _INTENT_PROTOTYPES is None:
            flat = [s for exemplars in _INTENT_EXEMPLARS.values() for s in exemplars]
            vectors = await embed(flat)
            prototypes: dict[SearchIntent, list[float]] = {}
            idx = 0
            counts: dict[SearchIntent, int] = {}
            sums: dict[SearchIntent, list[float]] = {}
            for intent, exemplars in _INTENT_EXEMPLARS.items():
                for _ in exemplars:
                    vec = vectors[idx]
                    idx += 1
                    sums.setdefault(intent, [0.0] * len(vec))
                    counts.setdefault(intent, 0)
                    acc = sums[intent]
                    for i, v in enumerate(vec):
                        acc[i] += v
                    counts[intent] += 1
            for intent, acc in sums.items():
                n = float(counts[intent])
                prototypes[intent] = [v / n for v in acc]
            _INTENT_PROTOTYPES = prototypes
        query_vec = await embed(text)
        scores = {intent: _cosine(query_vec, proto) for intent, proto in _INTENT_PROTOTYPES.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) < 2:
            return None
        (top_intent, top_score), (_, second_score) = ranked[0], ranked[1]
        if (top_score - second_score) < margin:
            return None
        return top_intent
    except Exception as exc:  # embedder failure is an abstention, never a crash
        _LOGGER.debug("embedding intent abstained: %s", exc)
        return None


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
    ``intent_override`` (e.g. from ``classify_intent_by_embedding``) is applied
    after the deterministic coarse pass, when provided.

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
        rules.append(f"intent.embedding:{intent}")
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


_LOGGER = logging.getLogger(__name__)
