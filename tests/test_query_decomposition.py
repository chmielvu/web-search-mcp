from __future__ import annotations

from kindly_web_search_mcp_server.search.query_decomposition import (
    CLASSIFIER_JSON_SCHEMA,
    DECOMPOSITION_JSON_SCHEMA,
    build_classifier_messages,
    build_decomposition_messages,
    normalize_sub_questions,
)
from kindly_web_search_mcp_server.search.query_rewrite_models import (
    QueryDecompositionOutput,
    SubQuestion,
)


def test_build_classifier_messages_include_context() -> None:
    messages = build_classifier_messages(
        query="FastMCP prompt timeout",
        research_goal="debug prompt timeout behavior",
        must_keep_terms=["FastMCP"],
    )

    assert messages[0]["role"] == "system"
    assert "FastMCP" in messages[1]["content"]
    assert "debug prompt timeout behavior" in messages[1]["content"]
    assert CLASSIFIER_JSON_SCHEMA["required"] == [
        "intent",
        "should_decompose",
        "confidence",
        "routing",
    ]


def test_build_decomposition_messages_include_routing() -> None:
    messages = build_decomposition_messages(
        query="React 19 vs Vue 4 SSR performance",
        research_goal="compare frameworks",
        must_keep_terms=["React 19", "Vue 4"],
        intent="comparison",
        routing={"keyword": True, "neural": True, "community": True},
    )

    assert messages[0]["role"] == "system"
    assert "React 19" in messages[1]["content"]
    assert "keyword: True" in messages[1]["content"]
    assert DECOMPOSITION_JSON_SCHEMA["properties"]["sub_questions"]["maxItems"] == 3


def test_normalize_sub_questions_deduplicates_and_trims() -> None:
    output = QueryDecompositionOutput(
        should_decompose=True,
        sub_questions=[
            SubQuestion(
                question="  FastMCP docs  ",
                target="keyword",
                why=" docs ",
                weight=1.0,
            ),
            SubQuestion(
                question="FastMCP docs",
                target="keyword",
                why="duplicate",
                weight=1.0,
            ),
            SubQuestion(
                question="community issues",
                target="community",
                why=" community ",
                weight=1.0,
            ),
        ],
    )

    normalized = normalize_sub_questions(output, max_subquestions=3)

    assert normalized.should_decompose is True
    assert [item.question for item in normalized.sub_questions] == [
        "FastMCP docs",
        "community issues",
    ]
    assert normalized.sub_questions[0].why == "docs"
