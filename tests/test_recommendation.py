from __future__ import annotations

from kindly_web_search_mcp_server.recommendation import build_command_recommendation


def test_known_url_summary_routes_to_unified_fetch_with_ai_summary() -> None:
    result = build_command_recommendation("Summarize https://docs.example.com/guide")

    assert result.intent == "known_url_read"
    assert result.recommended_route.mcp_tool == "fetch"
    assert "content fetch" in result.recommended_command
    assert "--ai-summary" in result.recommended_command
    assert result.recommended_route.arguments["url"] == "https://docs.example.com/guide"


def test_github_implementation_routes_to_deep_code_search() -> None:
    result = build_command_recommendation(
        "Find the implementation of retries in https://github.com/owner/project"
    )

    assert result.intent == "github_code_or_docs"
    assert result.recommended_route.mcp_tool == "code_search"
    assert result.recommended_route.arguments["--repository"] == "owner/project"
    assert result.recommended_route.arguments["--deep"] is True
    assert "search code" in result.recommended_command


def test_academic_complex_task_requires_decomposition() -> None:
    result = build_command_recommendation(
        "Compare primary papers, benchmarks, mechanisms, counterevidence, and mitigations "
        "for catastrophic forgetting in continued training, covering data quality, code skill, "
        "math reasoning, and production trade-offs."
    )

    assert result.intent == "academic_search"
    assert result.decomposition_required is True
    assert result.orchestration_strategy == "split_then_route"
    assert len(result.decomposition_rules) == 3


def test_quick_recon_has_grounded_fallback() -> None:
    result = build_command_recommendation("Quickly map the landscape of MCP server frameworks")

    assert result.intent == "quick_reconnaissance"
    assert result.fallback_routes
    assert result.fallback_routes[0].intent == "grounded_answer"
    assert "ai gemini" in result.fallback_commands[0]


def test_multiple_known_urls_routes_to_unified_fetch() -> None:
    result = build_command_recommendation(
        "Read https://example.com/a, https://example.com/b, and https://example.com/c"
    )

    assert result.intent == "multi_url_read"
    assert result.recommended_route.mcp_tool == "fetch"
    assert result.recommended_route.arguments["urls"] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
