from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..validation import (
    InputValidationError,
    validate_http_url,
    validate_safe_path,
    validate_text_input,
)


def _url_from_record(record: Any, *, line_number: int) -> str:
    if isinstance(record, str):
        value = record.strip()
    elif isinstance(record, dict):
        value = str(record.get("url") or "").strip()
    else:
        value = ""
    if not value:
        raise ValueError(f"Input line {line_number} does not contain a non-blank url.")
    try:
        return validate_http_url(value, field=f"input file line {line_number} url")
    except InputValidationError as exc:
        # Keep the public ValueError API so fetch/crawl map this to a
        # structured usage_error at the command boundary.
        raise ValueError(f"Input line {line_number}: {exc}") from exc


def read_url_inputs(
    urls: list[str] | None,
    input_file: str | None,
) -> list[str] | None:
    values: list[str] = []
    for raw in urls or []:
        if not raw or not raw.strip():
            continue
        try:
            cleaned = validate_http_url(raw, field="--url")
        except InputValidationError as exc:
            raise ValueError(str(exc)) from exc
        values.append(cleaned)

    if input_file is None:
        return values or None

    # ``-`` is the documented stdin sentinel; let it through before path
    # validation so the file-system checks do not reject a literal dash.
    if input_file == "-":
        try:
            text = sys.stdin.read()
        except OSError as exc:
            raise ValueError(f"Failed to read stdin: {exc}") from exc
        lines = text.splitlines()
    else:
        safe_path = validate_safe_path(input_file, field="--input-file")
        try:
            lines = Path(safe_path).expanduser().read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"Failed to read --input-file {safe_path!r}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        # Validate free-form text first so JSONL records whose ``url``
        # contains control characters fail before JSON parsing blows up on
        # them. ``validate_text_input`` already strips control characters and
        # rejects credential-shaped material.
        try:
            cleaned_line = validate_text_input(stripped, field=f"input line {line_number}")
        except InputValidationError as exc:
            raise ValueError(str(exc)) from exc
        if cleaned_line.startswith("{"):
            try:
                record = json.loads(cleaned_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Input line {line_number} is not valid JSON: {exc.msg}") from exc
            values.append(_url_from_record(record, line_number=line_number))
        else:
            values.append(_url_from_record(cleaned_line, line_number=line_number))
    return values or None
