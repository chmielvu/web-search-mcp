from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..errors import raise_tool_error
from ..recommendation import CommandRecommendation, build_command_recommendation


def recommend_command(
    task: Annotated[
        str,
        Field(
            description=(
                "Natural-language task. The tool recommends an existing CLI/MCP route only; "
                "it never executes commands or provider calls."
            )
        ),
    ],
) -> CommandRecommendation:
    """Recommend the safest existing route for a natural-language task."""
    try:
        return build_command_recommendation(task)
    except Exception as exc:
        raise_tool_error(exc, provider="recommend_command")


__all__ = ["recommend_command"]
