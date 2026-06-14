from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from .runtime import get_runtime


SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_json(data: dict[str, Any], *, command: str) -> None:
    runtime = get_runtime()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "data": data,
        "meta": {
            "command": command,
            "profile": data.get("profile", runtime.profile),
            "output_mode": runtime.output_mode,
            "quiet": runtime.quiet,
            "log_level": runtime.log_level,
            "non_interactive": runtime.non_interactive,
            "duration_ms": 0,
            "generated_at": utc_now(),
        },
        "suggested_next": [],
    }
    indent = 2 if runtime.output_mode == "human" else None
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")


def emit_error(payload: dict[str, Any]) -> None:
    runtime = get_runtime()
    indent = 2 if runtime.output_mode == "human" else None
    sys.stderr.write(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")
