from __future__ import annotations

from fastmcp.prompts import Message


def web_search_workflow_prompt() -> list[Message]:
    """Placeholder prompt for web search workflow."""
    return [
        Message(
            "Placeholder — the full research workflow lives at the docs://workflow resource.",
            role="user",
        ),
    ]
