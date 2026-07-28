"""Pure-Python validation, deduplication, and overlap merging for entities."""

from __future__ import annotations

import re
from typing import Iterable

from .models import EntitySpan


_VERSION_V_PREFIX = re.compile(r"^v(?=\d)", re.IGNORECASE)
_REPO_REF_VALID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_.-]+)?$")
_VERSION_VALID = re.compile(r"^\d+(?:\.\d+)*(?:[-+._A-Za-z0-9]+)?$")


def _canonical_text(text: str, label: str) -> str:
    """Return a comparison key without changing the source-facing text."""
    value = text.strip().strip(".,;:!?()[]{}<>\"'` ")
    if label == "version":
        value = _VERSION_V_PREFIX.sub("", value)
    return value


def _is_valid_for_label(text: str, label: str) -> bool:
    value = _canonical_text(text, label)
    if not value or len(value) < 2:
        return False
    if label == "repo_ref":
        return bool(_REPO_REF_VALID.fullmatch(value))
    if label == "version":
        return bool(_VERSION_VALID.fullmatch(value)) or bool(re.search(r"\d", value))
    if label in {"package", "api_function", "error_class", "model_id", "env_var"}:
        return not bool(re.fullmatch(r"[\W_]+", value))
    if label == "file_path":
        return any(char in value for char in ("/", "\\", "."))
    return True


def _spans_overlap(a: EntitySpan, b: EntitySpan) -> bool:
    if a.start is None or a.end is None or b.start is None or b.end is None:
        return False
    return not (a.end <= b.start or b.end <= a.start)


def _merge_two(a: EntitySpan, b: EntitySpan) -> EntitySpan:
    """Keep the longer grounded surface, breaking ties by confidence."""
    a_length = (a.end or 0) - (a.start or 0)
    b_length = (b.end or 0) - (b.start or 0)
    if a_length > b_length or (
        a_length == b_length and (a.confidence or 0.0) >= (b.confidence or 0.0)
    ):
        base, other = a, b
    else:
        base, other = b, a
    confidence = max(base.confidence or 0.0, other.confidence or 0.0)
    return base.model_copy(update={"confidence": confidence if confidence > 0 else None})


def _dedup_key(entity: EntitySpan) -> tuple[object, ...]:
    if entity.start is not None and entity.end is not None:
        return (entity.label, entity.start, entity.end)
    return (entity.label, _canonical_text(entity.text, entity.label).casefold())


def postprocess_entities(entities: Iterable[EntitySpan]) -> list[EntitySpan]:
    """Validate entities while preserving their exact source surface text.

    Distinct non-overlapping occurrences with the same text remain distinct.
    Only identical grounded offsets are deduplicated, and overlapping spans of
    the same label are merged after validation.
    """
    valid = [entity for entity in entities if _is_valid_for_label(entity.text, entity.label)]
    if not valid:
        return []

    deduped: dict[tuple[object, ...], EntitySpan] = {}
    for entity in valid:
        key = _dedup_key(entity)
        existing = deduped.get(key)
        if existing is None or (entity.confidence or 0.0) > (existing.confidence or 0.0):
            deduped[key] = entity

    by_label: dict[str, list[EntitySpan]] = {}
    for entity in deduped.values():
        by_label.setdefault(entity.label, []).append(entity)

    merged: list[EntitySpan] = []
    for label, group in by_label.items():
        group.sort(
            key=lambda item: (item.start if item.start is not None else 10**9, item.end or 0)
        )
        kept: list[EntitySpan] = []
        for entity in group:
            for index, existing in enumerate(kept):
                if existing.label == label and _spans_overlap(existing, entity):
                    kept[index] = _merge_two(existing, entity)
                    break
            else:
                kept.append(entity)
        merged.extend(kept)

    merged.sort(
        key=lambda item: (item.start if item.start is not None else 10**9, item.label, item.text)
    )
    return merged
