"""Normalized result schema shared by all sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Result:
    title: str
    url: str
    snippet: str = ""
    source: str = ""      # searxng | exa | twitter | reddit | github | youtube | web
    engine: str = ""      # underlying engine/backend (bing, twitter-cli, opencli, ...)
    published: str | None = None
    score: float = 0.0    # final score (RRF and/or re-rank)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # never let None sneak into string fields — downstream (fusion, dedup)
        # slices these unconditionally (CN smoke-test discovery 2026-08-25)
        if self.snippet is None:
            self.snippet = ""
        if self.title is None:
            self.title = ""
        if self.url is None:
            self.url = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def identity(self) -> str:
        """Stable key for cross-source dedup."""
        return self.url.strip().rstrip("/") if self.url else self.title.strip().lower()
