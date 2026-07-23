from __future__ import annotations

from dataclasses import dataclass, field
import re
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
                "code": self.kind,
                "message": self.message,
                "hint": self.hint,
                "suggestion": self.hint,
                "exit_code": int(self.exit_code),
                "context": self.context,
            }
        }


@dataclass(frozen=True)
class HintRule:
    pattern: str
    kind: str
    hint: str
    exit_code: ExitCode


HINT_RULES: list[HintRule] = [
    HintRule(
        r"401|unauthorized|invalid.?token|api.?key",
        "auth_error",
        "Set the required API key environment variable (e.g. BRAVE_API_KEY, TAVILY_API_KEY, GEMINI_API_KEY).",
        ExitCode.AUTH_ERROR,
    ),
    HintRule(
        r"404|not.?found|missing|unknown.?command",
        "not_found",
        "Run `web-search-cli schema` or `web-search-cli reference tools` to see valid commands and resources.",
        ExitCode.NOT_FOUND,
    ),
    HintRule(
        r"rate.?limit|429|too.?many",
        "rate_limited",
        "Wait 60 seconds and retry. Upstream rate limit reached.",
        ExitCode.RATE_LIMITED,
    ),
    HintRule(
        r"already.?exists|conflict|duplicate",
        "conflict",
        "Resource already exists. Safe to skip or pass --if-not-exists flag.",
        ExitCode.CONFLICT,
    ),
    HintRule(
        r"network|connection.?refused|timeout|dns",
        "network_error",
        "Check network connectivity and endpoint status, then retry.",
        ExitCode.NETWORK_ERROR,
    ),
]


def match_hint_rule(message: str) -> HintRule | None:
    for rule in HINT_RULES:
        if re.search(rule.pattern, message, re.IGNORECASE):
            return rule
    return None
