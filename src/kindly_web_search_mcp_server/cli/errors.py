from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .exit_codes import ExitCode


@dataclass(slots=True)
class CliError(Exception):
    kind: str
    message: str
    hint: str
    exit_code: ExitCode = ExitCode.INTERNAL_ERROR
    context: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "kind": self.kind,
                "message": self.message,
                "hint": self.hint,
                "context": self.context,
            }
        }


def scaffold_error(command: str) -> CliError:
    return CliError(
        kind="usage_error",
        message=f"`web-search-cli {command}` is planned but not implemented in the scaffolding phase.",
        hint="Run `web-search-cli schema` or `web-search-cli reference tools` to inspect the planned surface.",
        exit_code=ExitCode.USAGE_ERROR,
        context={"command": command, "phase": "scaffolding"},
    )
