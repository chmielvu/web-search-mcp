from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from .filters import TemporalWindow


@dataclass(frozen=True, slots=True)
class SearchOptions:
    temporal: TemporalWindow | None = None
    language: str | None = None
    region: str | None = None

    def validate(self) -> SearchOptions:
        if self.temporal is not None and self.temporal.bucket is None and self.temporal.is_empty:
            raise ValueError("temporal window resolved empty; pass None instead.")
        return self

    def cache_fingerprint(self) -> str:
        payload = {
            "temporal": (
                {
                    "start": self.temporal.start.isoformat() if self.temporal.start else None,
                    "end": self.temporal.end.isoformat() if self.temporal.end else None,
                    "bucket": self.temporal.bucket,
                }
                if self.temporal is not None
                else None
            ),
            "language": self.language,
            "region": self.region,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()[:16]
