from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.prompts.rerank import build_cross_encoder_query


GENERAL = (
    "query | Prefer primary sources, original documentation, and in-depth content. "
    "Demote SEO listicles, aggregator pages, and ads-heavy sites. | Research goal: goal"
)
TECHNICAL = (
    "query | Prefer official documentation, primary source code, GitHub repositories, and "
    "authoritative technical writing (e.g. arXiv, ACM, IEEE, vendor docs). Demote tutorials "
    'on low-quality blogs, content farms, and SEO-optimized "best {N} tools" listicles. '
    "| Research goal: goal"
)
NEWS = (
    "query | Prefer recent, in-depth reporting from primary news outlets and topic experts. "
    "Demote press-release aggregators, syndicated copies, and content that recycles older "
    "reporting without original sourcing. | Research goal: goal"
)
ACADEMIC = (
    "query | Prefer peer-reviewed papers, preprints from recognized authors, and authoritative "
    "surveys. Demote blog posts, vendor whitepapers, and non-peer-reviewed secondary sources "
    "unless they cite primary work. | Research goal: goal"
)


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("general", GENERAL),
        ("comparison", GENERAL),
        ("social_media", GENERAL),
        (None, GENERAL),
        ("unknown", GENERAL),
        ("ai_coding_and_infrastructure", TECHNICAL),
        ("news", NEWS),
        ("digital_humanities", ACADEMIC),
    ],
)
def test_build_cross_encoder_query_exact_templates(intent: str | None, expected: str) -> None:
    assert build_cross_encoder_query(" query ", intent, " goal ") == expected


def test_build_cross_encoder_query_rejects_blank_goal() -> None:
    with pytest.raises(ValueError, match="research_goal must be non-blank"):
        build_cross_encoder_query("query", "general", " \n ")


def test_build_cross_encoder_query_caps_only_goal_to_500_characters() -> None:
    query = "q" * 600
    output = build_cross_encoder_query(query, "general", "g" * 600)
    prefix, goal = output.rsplit(" | Research goal: ", 1)
    assert prefix.startswith(query)
    assert len(goal) == 500


def test_build_cross_encoder_query_with_reranking_instructions() -> None:
    output = build_cross_encoder_query(
        "query", "general", "goal", reranking_instructions="Prioritize GitHub repos."
    )
    assert "Caller reranking instructions: Prioritize GitHub repos." in output
    assert output.endswith("Research goal: goal")
