from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _url_from_record(record: Any, *, line_number: int) -> str:
    if isinstance(record, str):
        value = record.strip()
    elif isinstance(record, dict):
        value = str(record.get("url") or record.get("input_url") or "").strip()
    else:
        value = ""
    if not value:
        raise ValueError(f"Input line {line_number} does not contain a non-blank url.")
    return value


def read_url_inputs(
    urls: list[str] | None,
    input_file: str | None,
) -> list[str] | None:
    values = [value.strip() for value in (urls or []) if value and value.strip()]
    if input_file is None:
        return values or None

    if input_file == "-":
        lines = sys.stdin.read().splitlines()
    else:
        lines = Path(input_file).expanduser().read_text(encoding="utf-8").splitlines()

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Input line {line_number} is not valid JSON: {exc.msg}") from exc
            values.append(_url_from_record(record, line_number=line_number))
        else:
            values.append(_url_from_record(stripped, line_number=line_number))
    return values or None
