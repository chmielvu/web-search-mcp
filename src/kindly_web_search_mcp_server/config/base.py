"""Shared helper parsers used by config section models."""

from __future__ import annotations

import json


def parse_csv_env(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated environment string into a normalised tuple."""
    items: list[str] = []
    for item in raw.split(","):
        value = item.strip().casefold()
        if value:
            items.append(value)
    return tuple(dict.fromkeys(items))


def parse_json_dict_env(
    raw: str, default: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Parse a JSON object env string into a dict of non-empty string lists.

    Raises ValueError (caught at Settings construction) on invalid JSON or on
    any key/value that is not a non-empty string / non-empty list of strings.
    """
    if not raw or not raw.strip():
        return dict(default)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"BRAVE_GOGGLES_BY_INTENT must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("BRAVE_GOGGLES_BY_INTENT must be a JSON object.")
    cleaned: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("BRAVE_GOGGLES_BY_INTENT keys must be non-empty strings.")
        if not isinstance(value, list) or not value:
            raise ValueError(f"BRAVE_GOGGLES_BY_INTENT[{key!r}] must be a non-empty list.")
        items = [str(v).strip() for v in value]
        if not all(items):
            raise ValueError(f"BRAVE_GOGGLES_BY_INTENT[{key!r}] entries must be non-empty strings.")
        cleaned[key.strip()] = items
    return cleaned


def redact_secret(value: str | None) -> str:
    """Return '***REDACTED***' for any non-empty secret, else ''."""
    if value:
        return "***REDACTED***"
    return ""
