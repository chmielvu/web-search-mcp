"""Prompt helpers for rerank instruction steering."""

from __future__ import annotations


def build_cross_encoder_query(
    user_query: str,
    query_type: str | None,
    research_goal: str,
    reranking_instructions: str | None = None,
) -> str:
    if not research_goal or not research_goal.strip():
        raise ValueError("research_goal must be non-blank")

    user_query = " ".join(user_query.split()).strip()
    qtype = (query_type or "general").strip().lower()

    if qtype in ("general", "comparison", "social_media"):
        template = "{user_query} | Prefer primary sources, original documentation, and in-depth content. Demote SEO listicles, aggregator pages, and ads-heavy sites."
    elif qtype == "ai_coding_and_infrastructure":
        template = '{user_query} | Prefer official documentation, primary source code, GitHub repositories, and authoritative technical writing (e.g. arXiv, ACM, IEEE, vendor docs). Demote tutorials on low-quality blogs, content farms, and SEO-optimized "best {{N}} tools" listicles.'
    elif qtype == "news":
        template = "{user_query} | Prefer recent, in-depth reporting from primary news outlets and topic experts. Demote press-release aggregators, syndicated copies, and content that recycles older reporting without original sourcing."
    elif qtype == "digital_humanities":
        template = "{user_query} | Prefer peer-reviewed papers, preprints from recognized authors, and authoritative surveys. Demote blog posts, vendor whitepapers, and non-peer-reviewed secondary sources unless they cite primary work."
    else:
        template = "{user_query} | Prefer primary sources, original documentation, and in-depth content. Demote SEO listicles, aggregator pages, and ads-heavy sites."

    formatted = template.format(user_query=user_query)
    capped_goal = research_goal.strip()[:500]

    if reranking_instructions and reranking_instructions.strip():
        instructions_segment = f" Caller reranking instructions: {reranking_instructions.strip()} |"
    else:
        instructions_segment = ""

    result = f"{formatted} |{instructions_segment} Research goal: {capped_goal}"
    return " ".join(result.split()).strip()
