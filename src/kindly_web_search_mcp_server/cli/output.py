from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from .runtime import get_runtime


SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit_json(
    data: dict[str, Any],
    *,
    command: str,
    duration_ms: float | None = None,
) -> None:
    runtime = get_runtime()
    from .metadata import feedback_guidance, rules_full, skill_catalog

    final_data: Any = data
    if runtime.fields and isinstance(data, dict):
        wanted = {f.strip() for f in runtime.fields.split(",") if f.strip()}
        final_data = {k: v for k, v in data.items() if k in wanted}

    if runtime.raw:
        if isinstance(final_data, (list, tuple)):
            for item in final_data:
                sys.stdout.write(str(item) + "\n")
        elif isinstance(final_data, dict) and len(final_data) == 1:
            sys.stdout.write(str(next(iter(final_data.values()))) + "\n")
        else:
            sys.stdout.write(json.dumps(final_data, ensure_ascii=False) + "\n")
        return

    profile_val = (
        data.get("profile", runtime.profile) if isinstance(data, dict) else runtime.profile
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "data": final_data,
        "meta": {
            "command": command,
            "profile": profile_val,
            "quiet": runtime.quiet,
            "log_level": runtime.log_level,
            "log_format": runtime.log_format,
            "debug": runtime.debug,
            "non_interactive": runtime.non_interactive,
            "raw": runtime.raw,
            "fields": runtime.fields,
            "yes": runtime.yes,
            "dry_run": runtime.dry_run,
            "duration_ms": round(
                duration_ms if duration_ms is not None else runtime.last_duration_ms,
                1,
            ),
            "generated_at": utc_now(),
        },
        "suggested_next": [],
    }
    if not runtime.quiet:
        payload["rules"] = rules_full()
        payload["skills"] = skill_catalog()
        payload["feedback"] = feedback_guidance()
    indent = None
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")


def emit_error(payload: dict[str, Any]) -> None:
    runtime = get_runtime()
    from .metadata import feedback_guidance, rules_full, skill_catalog

    err_payload = dict(payload)
    if not runtime.quiet:
        err_payload.setdefault("rules", rules_full())
        err_payload.setdefault("skills", skill_catalog())
        err_payload.setdefault("feedback", feedback_guidance())
    indent = None
    sys.stderr.write(json.dumps(err_payload, ensure_ascii=False, indent=indent) + "\n")
