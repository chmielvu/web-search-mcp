"""Post-processing for raw entity spans: validate, dedup, overlap-merge, normalize.

Pure-Python implementation. Called after chunk-wise extraction + offset correction.
No GLiNER2 dependency.
"""

from __future__ import annotations

import re
from typing import Iterable

from .models import EntitySpan


_VERSION_V_PREFIX = re.compile(r"^v(?=\d)", re.IGNORECASE)
_REPO_REF_VALID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_.-]+)?$")
# Loose but useful validators for common labels
_VERSION_VALID = re.compile(r"^\d+(?:\.\d+)*(?:[-+._A-Za-z0-9]+)?$")


def _normalize_text(text: str, label: str) -> str:
    t = text.strip()
    # strip leading/trailing punctuation that often bleeds into spans
    t = t.strip(".,;:!?()[]{}<>\"'` ")
    if label == "version":
        t = _VERSION_V_PREFIX.sub("", t)
    return t


def _is_valid_for_label(text: str, label: str) -> bool:
    t = text.strip()
    if not t or len(t) < 2:
        return False
    if label == "repo_ref":
        return bool(_REPO_REF_VALID.match(t))
    if label == "version":
        # after norm, should look like version
        normed = _normalize_text(t, label)
        return bool(_VERSION_VALID.match(normed)) or bool(re.search(r"\d", normed))
    if label in {"package", "api_function", "error_class", "model_id", "env_var"}:
        # disallow pure punctuation or very short junk
        if re.match(r"^[\W_]+$", t):
            return False
    if label == "file_path":
        if not any(c in t for c in ("/", "\\", ".")):
            return False
    return True


def _spans_overlap(a: EntitySpan, b: EntitySpan) -> bool:
    if a.start is None or a.end is None or b.start is None or b.end is None:
        return False
    return not (a.end <= b.start or b.end <= a.start)


def _merge_two(a: EntitySpan, b: EntitySpan) -> EntitySpan:
    """Merge two overlapping same-label spans: keep longer span + higher conf."""
    if (a.end or 0) - (a.start or 0) >= (b.end or 0) - (b.start or 0):
        base = a
        other = b
    else:
        base = b
        other = a
    conf = max(base.confidence or 0.0, other.confidence or 0.0)
    return base.model_copy(update={"confidence": conf if conf > 0 else None})


def postprocess_entities(entities: Iterable[EntitySpan]) -> list[EntitySpan]:
    """Apply validation, normalization, deduplication, and overlap merging.

    Order of operations (inspired by production GLiNER post-pipelines):
    1. Filter by label-specific validators.
    2. Normalize text (strip punct, version canonicalization).
    3. Dedup by (label, normalized_text.lower()), keep max confidence.
    4. Merge remaining same-label overlapping spans (keep best).
    5. Sort by start offset (stable).
    """
    # 1+2: validate + normalize (update in place copy)
    valid: list[EntitySpan] = []
    for e in entities:
        norm_text = _normalize_text(e.text, e.label)
        if not _is_valid_for_label(norm_text, e.label):
            continue
        valid.append(e.model_copy(update={"text": norm_text}))

    if not valid:
        return []

    # 3: dedup by (label, lower text) -> max conf
    deduped: dict[tuple[str, str], EntitySpan] = {}
    for e in valid:
        key = (e.label, e.text.lower())
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = e
        else:
            # keep higher confidence; prefer earlier start on tie
            if (e.confidence or 0.0) > (existing.confidence or 0.0) or (
                (e.confidence or 0.0) == (existing.confidence or 0.0)
                and (e.start or 0) < (existing.start or 0)
            ):
                deduped[key] = e

    candidates = list(deduped.values())

    # 4: merge overlaps per label
    by_label: dict[str, list[EntitySpan]] = {}
    for e in candidates:
        by_label.setdefault(e.label, []).append(e)

    merged: list[EntitySpan] = []
    for label, group in by_label.items():
        # sort by start for sweep
        group.sort(key=lambda x: (x.start or 0, -(x.end or 0)))
        kept: list[EntitySpan] = []
        for e in group:
            merged_with = False
            for i, k in enumerate(kept):
                if _spans_overlap(k, e) and k.label == e.label:
                    kept[i] = _merge_two(k, e)
                    merged_with = True
                    break
            if not merged_with:
                kept.append(e)
        merged.extend(kept)

    # 5: global sort by start (None last)
    merged.sort(key=lambda x: (x.start if x.start is not None else 10**9, x.label, x.text))
    return merged
