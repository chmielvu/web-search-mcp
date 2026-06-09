from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_json(data: dict[str, Any], *, command: str) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "data": data,
        "meta": {
            "command": command,
            "profile": data.get("profile", "full"),
            "duration_ms": 0,
            "generated_at": utc_now(),
        },
        "suggested_next": [],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def emit_error(payload: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
