from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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
    content_type: str | None
    markdown: str
    title: str | None = None
    word_count: int = 0
    quality_score: float = 0.0
    error: ContentError | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return len(self.markdown)
