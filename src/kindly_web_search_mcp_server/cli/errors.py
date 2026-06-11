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
