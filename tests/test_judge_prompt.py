"""Tests for the judge_prompt module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.analytics.judge_prompt import (
    JUDGE_SYSTEM_PROMPT,
    build_judge_user_prompt,
    parse_judge_response,
)


class TestBuildJudgeUserPrompt:
    """Tests for build_judge_user_prompt."""

    def test_returns_string_with_query_and_results(self) -> None:
        query = "latest AI research papers"
        intent = "informational"
        results = "1. Paper A (2024) - Transformers\n2. Paper B (2024) - LLMs"
        tool_name = "web_search"

        prompt = build_judge_user_prompt(query, intent, results, tool_name)

        assert isinstance(prompt, str)
        assert query in prompt
        assert results in prompt
        assert intent in prompt
        assert tool_name in prompt

    def test_contains_expected_sections(self) -> None:
        prompt = build_judge_user_prompt("test", "navigational", "result1", "news_search")
        assert "Tool used:" in prompt
        assert "User query:" in prompt
        assert "Search intent:" in prompt
        assert "--- Search results ---" in prompt
        assert "--- End results ---" in prompt
        assert "Please evaluate the quality" in prompt


class TestParseJudgeResponse:
    """Tests for parse_judge_response."""

    VALID_SCORES = {
        "relevance_score": 0.9,
        "accuracy_score": 0.85,
        "completeness_score": 0.75,
        "source_quality_score": 0.8,
        "overall_score": 0.82,
        "rationale": "Good results overall.",
    }

    def test_valid_json_returns_correct_dict(self) -> None:
        result = parse_judge_response(json.dumps(self.VALID_SCORES))

        assert result["relevance_score"] == 0.9
        assert result["accuracy_score"] == 0.85
        assert result["completeness_score"] == 0.75
        assert result["source_quality_score"] == 0.8
        assert result["overall_score"] == 0.82
        assert result["rationale"] == "Good results overall."

    def test_markdown_wrapped_json_parses_correctly(self) -> None:
        raw = f"```json\n{json.dumps(self.VALID_SCORES)}\n```"
        result = parse_judge_response(raw)

        assert result["relevance_score"] == 0.9
        assert result["accuracy_score"] == 0.85
        assert result["completeness_score"] == 0.75
        assert result["overall_score"] == 0.82

    def test_malformed_input_returns_sensible_defaults(self) -> None:
        result = parse_judge_response("this is not json at all")

        assert result["relevance_score"] is None
        assert result["accuracy_score"] is None
        assert result["completeness_score"] is None
        assert result["source_quality_score"] is None
        assert result["overall_score"] is None
        assert result["rationale"] is None

    def test_empty_string_returns_defaults(self) -> None:
        result = parse_judge_response("")
        assert result["relevance_score"] is None
        assert result["overall_score"] is None
        assert result["rationale"] is None

    def test_none_input_returns_defaults(self) -> None:
        result = parse_judge_response(None)  # type: ignore[arg-type]
        assert result["relevance_score"] is None
        assert result["overall_score"] is None

    def test_partial_json_returns_missing_keys_as_none(self) -> None:
        raw = '{"relevance_score": 0.9, "rationale": "only set two fields"}'
        result = parse_judge_response(raw)

        assert result["relevance_score"] == 0.9
        assert result["rationale"] == "only set two fields"
        assert result["accuracy_score"] is None
        assert result["completeness_score"] is None
        assert result["source_quality_score"] is None
        assert result["overall_score"] is None

    def test_int_values_coerced_to_float(self) -> None:
        raw = json.dumps(
            {
                "relevance_score": 1,
                "accuracy_score": 0,
                "completeness_score": 0,
                "source_quality_score": 1,
                "overall_score": 1,
                "rationale": "int test",
            }
        )
        result = parse_judge_response(raw)
        assert isinstance(result["relevance_score"], float)
        assert result["relevance_score"] == 1.0

    def test_jagged_markdown_with_extra_text(self) -> None:
        raw = f"Here is my evaluation:\n```json\n{json.dumps(self.VALID_SCORES)}\n```\nHope this helps!"
        result = parse_judge_response(raw)

        assert result["relevance_score"] == 0.9
        assert result["overall_score"] == 0.82


class TestJudgeSystemPrompt:
    """Tests for the JUDGE_SYSTEM_PROMPT constant."""

    def test_contains_key_instructions(self) -> None:
        assert isinstance(JUDGE_SYSTEM_PROMPT, str)
        assert "search quality evaluator" in JUDGE_SYSTEM_PROMPT.lower()
        assert "relevance" in JUDGE_SYSTEM_PROMPT.lower()
        assert "accuracy" in JUDGE_SYSTEM_PROMPT.lower()
        assert "completeness" in JUDGE_SYSTEM_PROMPT.lower()
        assert "source_quality" in JUDGE_SYSTEM_PROMPT.lower()
        assert "overall_score" in JUDGE_SYSTEM_PROMPT
        assert "rationale" in JUDGE_SYSTEM_PROMPT
