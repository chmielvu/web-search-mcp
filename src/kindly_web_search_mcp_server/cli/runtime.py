from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OutputMode = Literal["agent", "human"]


@dataclass(slots=True)
class CliRuntime:
    output_mode: OutputMode = "agent"
    profile: str = "full"
    quiet: bool = False
    log_level: str = "error"
    non_interactive: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "output_mode": self.output_mode,
            "profile": self.profile,
            "quiet": self.quiet,
            "log_level": self.log_level,
            "non_interactive": self.non_interactive,
        }


_RUNTIME = CliRuntime()


def set_runtime(
    *,
    agent: bool = True,
    human: bool = False,
    quiet: bool = False,
    profile: str = "full",
    log_level: str = "error",
    non_interactive: bool = True,
) -> CliRuntime:
    runtime = CliRuntime(
        output_mode="human" if human else "agent",
        profile=profile,
        quiet=quiet,
        log_level=log_level,
        non_interactive=non_interactive,
    )
    global _RUNTIME
    _RUNTIME = runtime
    return runtime


def get_runtime() -> CliRuntime:
    return _RUNTIME
