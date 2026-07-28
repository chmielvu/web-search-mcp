from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.prompts.registry import build_prompt


@pytest.mark.parametrize("name", ["gemini_search", "grok_search", "rerank"])
def test_prompt_registry_renders_known_keys(name: str) -> None:
    system, user = build_prompt(
        name,
        query="FastAPI",
        research_goal="find docs",
        intent="general",
        provider_name="worker",
    )

    assert system
    assert user


def test_prompt_registry_rejects_unknown_keys() -> None:
    with pytest.raises(KeyError):
        build_prompt("missing", query="x")
