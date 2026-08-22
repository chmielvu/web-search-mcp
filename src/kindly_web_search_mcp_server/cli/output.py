from __future__ import annotations

import json
import shlex
import sys
from datetime import UTC, datetime
from typing import Any
from .runtime import get_runtime




SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _suggested_next(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []

    suggestions: list[str] = []
    primary = data
    if data.get("mode") == "single" and isinstance(data.get("results"), list) and data["results"]:
        primary = data["results"][0]
    window = primary.get("window") if isinstance(primary, dict) else None
    if isinstance(window, dict) and window.get("has_more"):
        url = primary.get("input_url") or primary.get("url")
        next_offset = window.get("next_offset")
        if isinstance(url, str) and next_offset is not None:
            suggestions.append(
                shlex.join(
                    [
                        "uv",
                        "run",
                        "web-search-cli",
                        "content",
                        "fetch",
                        "--url",
                        url,
                        "--offset",
                        str(next_offset),
                    ]
                )
            )

    if data.get("mode") == "bulk" and data.get("has_more") and data.get("cursor"):
        suggestions.append(
            shlex.join(
                [
                    "uv",
                    "run",
                    "web-search-cli",
                    "content",
                    "fetch",
                    "--cursor",
                    str(data["cursor"]),
                ]
            )
        )

    for continuation in data.get("next", [])[:3]:
        if not isinstance(continuation, dict):
            continue
        tool = continuation.get("tool")
        query = continuation.get("query")
        if not isinstance(query, dict):
            continue
        if tool == "code_fetch" and isinstance(query.get("repository"), str):
            args = [
                "uv",
                "run",
                "web-search-cli",
                "search",
                "fetch",
                "--repository",
                query["repository"],
            ]
            for key in ("query", "path", "symbol"):
                if query.get(key):
                    args.extend([f"--{key}", str(query[key])])
            suggestions.append(shlex.join(args))
        elif tool == "fetch" and isinstance(query.get("url"), str):
            args = [
                "uv",
                "run",
                "web-search-cli",
                "content",
                "fetch",
                "--url",
                query["url"],
            ]
            if query.get("focus_query"):
                args.extend(["--focus-query", str(query["focus_query"])])
            suggestions.append(shlex.join(args))

    return list(dict.fromkeys(suggestions))


def emit_json(
    data: dict[str, Any],
    *,
    command: str,
    duration_ms: float | None = None,
) -> None:
    runtime = get_runtime()
    from .metadata import feedback_guidance, rules_full, skill_catalog
    if command != "results search" and not command.endswith(" --help"):
        from .services.results import persist_cli_result

        persist_cli_result(command, data)
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
        "suggested_next": _suggested_next(data),
    }
    if isinstance(data, dict) and data.get("run_key"):
        payload["meta"]["run_key"] = data["run_key"]
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
