from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


def _normalize_selectors(value: str | None) -> list[str]:
    if not value:
        return []
    selectors = [selector.strip() for selector in value.split(",")]
    return [selector for selector in selectors if selector]


@dataclass(frozen=True)
class FetchOptions:
    include_metadata: bool = True
    include_links: bool = False
    max_links: int = 25
    strip_selectors: str | None = None

    def validate(self) -> None:
        if self.max_links < 1:
            raise ValueError("max_links must be at least 1")

    def selector_list(self) -> list[str]:
        return _normalize_selectors(self.strip_selectors)

    def cache_fingerprint(self) -> str:
        payload = "|".join(
            [
                str(self.include_metadata),
                str(self.include_links),
                str(self.max_links),
                self.strip_selectors or "",
            ]
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "include_metadata": self.include_metadata,
            "include_links": self.include_links,
            "max_links": self.max_links,
            "strip_selectors": self.strip_selectors,
        }


def build_fetch_options(
    *,
    include_metadata: bool = True,
    include_links: bool = False,
    max_links: int = 25,
    strip_selectors: str | None = None,
) -> FetchOptions:
    options = FetchOptions(
        include_metadata=include_metadata,
        include_links=include_links,
        max_links=max_links,
        strip_selectors=strip_selectors,
    )
    options.validate()
    return options
