"""Unified data models for search results."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class SearchResult:
    """A single search hit, normalized across providers."""

    url: str
    title: str
    snippet: str = ""
    provider: str = ""
    score: float = 0.0
    published: str | None = None
    sources: list[str] = field(default_factory=list)  # set by dedup merge
    content: str | None = None  # populated by --extract-top pipeline or --raw
    summary: str | None = None  # LLM-generated summary (e.g. Exa contents.summary)
    favicon: str | None = None  # populated by Tavily include_favicon=True
    author: str | None = None
    image: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.sources and self.provider:
            self.sources = [self.provider]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)  # keep machine output lean
        return d


@dataclass
class ProviderError:
    """Captures a non-fatal failure from a single provider."""

    provider: str
    message: str
    status: int | None = None
