from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
_INITIALIZE = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "startup-regression", "version": "1.0.0"},
            },
        }
    )
    + "\n"
    + json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
    )
    + "\n"
)


def _json_objects(stream: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for line in stream.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def test_stdio_initialize_stays_on_stdout_with_telemetry_enabled() -> None:
    env = os.environ.copy()
    env.update(
        {
            "OTEL_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "",
            "GRAFANA_CLOUD_OTLP_ENDPOINT": "",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-m", "kindly_web_search_mcp_server", "--stdio"],
        cwd=_REPO_ROOT,
        env=env,
        input=_INITIALIZE,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert completed.returncode == 0, (
        f"stdio server exited with {completed.returncode}; "
        f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
    )
    stdout_messages = _json_objects(completed.stdout)
    stderr_messages = _json_objects(completed.stderr)
    initialize_responses = [
        message
        for message in stdout_messages
        if message.get("jsonrpc") == "2.0"
        and message.get("id") == 1
        and isinstance(message.get("result"), dict)
    ]

    assert len(initialize_responses) == 1, (
        f"expected one initialize response on stdout; "
        f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
    )
    result = initialize_responses[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "web-search"
    assert not any(
        message.get("jsonrpc") == "2.0"
        and message.get("id") == 1
        and isinstance(message.get("result"), dict)
        for message in stderr_messages
    ), "initialize response was redirected to stderr"
