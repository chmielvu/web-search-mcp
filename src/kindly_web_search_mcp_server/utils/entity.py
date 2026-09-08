"""Entity contracts: models, label schemas, and source-grounded post-processing.

Pure-Python core shared by query understanding, YouTube transcript analysis,
and optional content extraction. Inference runs on the hosted GLiNER2
gateway (see ml/gliner_client.py); this module never imports it.
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_CONTENT_LABELS",
    "DEFAULT_CONTENT_RELATIONS",
    "_GRAPH_RELATIONS",
    "DEFAULT_QUERY_LABELS",
    "DEFAULT_QUERY_RELATIONS",
    "EntityRelation",
    "EntitySpan",
    "postprocess_entities",
]


class EntitySpan(BaseModel):
    """A source-grounded entity mention."""

    text: str = Field(description="Surface form exactly as it appears in source text.")
    label: str = Field(description="Entity label from the extraction schema.")
    start: int | None = Field(
        default=None,
        description="Character start offset (inclusive) in the source text.",
    )
    end: int | None = Field(
        default=None,
        description="Character end offset (exclusive) in the source text.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Model-assigned confidence for this span.",
    )

    model_config = {"extra": "forbid"}


class EntityRelation(BaseModel):
    """A validated relation between two source-grounded entity mentions.

    ``confidence`` is derived from the minimum endpoint confidence because
    GLiNER2 does not expose an independent relation score.
    """

    relation: str
    head: EntitySpan
    tail: EntitySpan
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}


# One vocabulary is shared by query understanding and content extraction.
DEFAULT_QUERY_LABELS: dict[str, str] = {
    "package": "Software package, library, or framework name",
    "version": "Software version string",
    "api_function": "API endpoint, function, or method name",
    "error_class": "Error or exception class name",
    "repo_ref": "GitHub or GitLab repository reference",
    "cli_flag": "Command-line flag or argument",
    "model_id": "Machine-learning model identifier",
    "file_path": "File or module path",
    "env_var": "Environment variable name",
    "person": "Person name",
    "organization": "Company, team, or organization",
    "date": "Date or time expression",
    "product": "Product, service, or platform product name",
    "url": "URL or web address",
    "language": "Programming, markup, or data language",
    "platform": "Operating system, runtime, hosting platform, or target environment",
    "provider": "Cloud, model, search, or API provider",
    "dataset": "Dataset or corpus name",
    "topic": "Named subject or technical topic",
    "tool": "Developer or command-line tool",
}

DEFAULT_QUERY_RELATIONS: dict[str, str] = {
    "compares_with": "One named software, model, provider, platform, product, or tool is compared with another",
    "version_of": "A package, product, or model is associated with its version",
    "uses": "A project, package, framework, or tool uses another package, API, model, or tool",
    "requires": "A package, project, or tool requires a dependency, version, API, or environment variable",
    "runs_on": "A package, model, or tool runs on or targets a platform, runtime, operating system, or provider",
    "implements": "A package, project, or framework implements an API, protocol, or interface",
}

# Content extraction reuses every query label and adds no second vocabulary.
DEFAULT_CONTENT_LABELS: dict[str, str] = dict(DEFAULT_QUERY_LABELS)
DEFAULT_CONTENT_RELATIONS: dict[str, str] = dict(DEFAULT_QUERY_RELATIONS)


_GRAPH_RELATIONS: tuple[tuple[str, str, str], ...] = (
    ("works_for", "person", "organization"),
    ("compares_with", "product", "product"),
    ("uses", "product", "package"),
    ("version_of", "version", "package"),
    ("runs_on", "package", "platform"),
)


# --- Post-processing: validation, deduplication, and overlap merging ---

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
