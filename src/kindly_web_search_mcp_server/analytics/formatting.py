"""Serialization helpers for analytics query/report outputs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_json_safe_value(row) for row in rows]
