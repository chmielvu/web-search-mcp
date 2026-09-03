from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..entity.models import EntitySpan  # pure, safe import


ContentStatus = Literal["success", "partial", "blocked", "unsupported", "error"]


@dataclass(frozen=True)
class ContentError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ContentArtifact:
    input_url: str
    normalized_url: str
    fetched_url: str | None
    status: ContentStatus
    source_type: str
    fetch_backend: str
    origin_backend: str | None = None
    cached: bool = False
    content_type: str | None = None
    markdown: str = ""
    title: str | None = None
    metadata: dict[str, Any] | None = None
    links: list[dict[str, Any]] | None = None
    word_count: int = 0
    quality_score: float = 0.0
    error: ContentError | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    entities: list[EntitySpan] | None = None  # populated when entity extraction enabled

    @property
    def total_chars(self) -> int:
        return len(self.markdown)


def artifact_to_dict(artifact: ContentArtifact) -> dict[str, Any]:
    """Canonical 15-key artifact dict shared by the fetch tool layer."""
    error = None
    if artifact.error is not None:
        error = {
            "code": artifact.error.code,
            "message": artifact.error.message,
            "retryable": artifact.error.retryable,
        }
    return {
        "input_url": artifact.input_url,
        "normalized_url": artifact.normalized_url,
        "fetched_url": artifact.fetched_url,
        "status": artifact.status,
        "source_type": artifact.source_type,
        "fetch_backend": artifact.fetch_backend,
        "origin_backend": artifact.origin_backend or artifact.fetch_backend,
        "cached": artifact.cached,
        "content_type": artifact.content_type,
        "markdown": artifact.markdown,
        "metadata": artifact.metadata,
        "links": artifact.links,
        "error": error,
        "entities": artifact.entities,
        "diagnostics": artifact.diagnostics,
    }
