"""The unified search-result schema every provider normalizes to.

Each provider module maps its own JSON response onto :class:`Result`, the single
row shape ``web_search`` returns regardless of which backend served the query::

    title    VARCHAR      result title
    url      VARCHAR      result URL
    snippet  VARCHAR      description / excerpt
    rank     INTEGER      1-based position in the result set (assigned by us)
    source   VARCHAR      provider name (e.g. 'brave')
    published TIMESTAMPTZ  publication time when the provider exposes one, else NULL
    score    DOUBLE       provider relevance score when available, else NULL
    extra    VARCHAR(JSON) provider-specific fields, JSON-encoded

``rank`` is assigned by the worker (1-based over the returned set), not taken
from the provider, so it is consistent across backends. ``extra`` is a JSON
string so the parameterized JSON return type needs no nested Arrow plumbing --
callers reach into it with DuckDB's ``->>`` / ``json_extract``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Result:
    """One normalized search hit (see module docstring for the column mapping)."""

    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    rank: int | None = None
    source: str | None = None
    published: datetime | None = None
    score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def extra_json(self) -> str | None:
        """JSON-encode ``extra`` (``None`` when empty, so the column reads NULL)."""
        if not self.extra:
            return None
        # ``default=str`` keeps a stray datetime/Decimal from crashing the encode.
        return json.dumps(self.extra, default=str, ensure_ascii=False)
