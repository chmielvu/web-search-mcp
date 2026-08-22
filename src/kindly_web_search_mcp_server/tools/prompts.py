from __future__ import annotations

from typing import Literal

from fastmcp.prompts import Message


def web_search_workflow_prompt(
    query: str,
    num_results: int = 5,
    depth: Literal["quick", "medium", "deep"] = "medium",
    focus: Literal["code", "academic", "news", "general"] = "general",
) -> list[Message]:
    """Guided research workflow.

    Args:
        query: the research question.
        num_results: target number of results (3=fast, 5=standard, 7=broad, max 10).
        depth: quick / medium / deep routing.
        focus: code / academic / news / general bias.
    """
    lines = [
        f"Research question: {query!r}",
        f"Target result count: {num_results} | Depth: {depth} | Focus: {focus}",
        "",
        "Follow this routing:",
    ]
    if depth == "quick":
        lines.append(
            "- QUICK: use quick_web_search for ranked excerpts/citations or gemini_search for a synthesized answer."
        )
    elif depth == "medium":
        lines.append(
            "- MEDIUM: web_search -> triage by provider_count>=2 -> fetch on top 2-3 URLs."
        )
    else:
        lines.append(
            "- DEEP: web_search(num_results=7) -> fetch with urls -> cross-check with academic_search."
        )
    if focus == "code":
        lines.append(
            "- CODE: bias toward github.com / stackoverflow.com; use rewrite=false for error hashes."
        )
    elif focus == "academic":
        lines.append(
            "- ACADEMIC: academic_search first (field/venue/year filters); cross-check 2+ papers."
        )
    elif focus == "news":
        lines.append("- NEWS: prefer grok_search for real-time/social signals.")
    else:
        lines.append("- GENERAL: balance docs/articles/community; docs first.")
    lines += [
        "",
        "Execution:",
        "1. Evaluate results: provider_count>=2 is a strong signal; verify domain if 1 or missing.",
        "2. Read pages: fetch with url or urls; on TimeoutError see docs://workflow.",
        "3. Gap analysis: terminate when 3 independent sources agree, or 2 consecutive rounds add nothing.",
    ]
    return [Message("\n".join(lines), role="user")]


def query_refinement_prompt(
    original_query: str,
    failed_attempts: list[str] | None = None,
    reason: str | None = None,
) -> list[Message]:
    """Plan query variants after a failed or sparse search.

    Args:
        original_query: the query that returned poor results.
        failed_attempts: queries already tried.
        reason: why the search failed (sparse, off-topic, blocked, ...).
    """
    tried = failed_attempts or []
    lines = [
        f"Original query: {original_query!r}",
        f"Failed attempts: {', '.join(tried) if tried else 'None'}",
        f"Failure reason: {reason or 'Sparse or off-topic results'}",
        "",
        "Generate 3 query variants:",
        "1. Broaden: replace jargon with general terms.",
        "2. Pinpoint: exact literals (rewrite=false) or double quotes.",
        "3. Decompose: split into independent sub-queries.",
    ]
    return [Message("\n".join(lines), role="user")]


def research_methodology_prompt() -> list[Message]:
    """The complete web research methodology — principles, patterns, and anti-patterns.

    Request this prompt when you need a refresher on how to do thorough web research
    with this server, or when starting a complex multi-round investigation.
    """
    return [
        Message(
            "\n".join(
                [
                    "WEB RESEARCH METHODOLOGY",
                    "",
                    "## The Core Loop",
                    "",
                    "Research is iterative, not transactional. The loop is:",
                    "1. DECOMPOSE — break the user's question into independent sub-questions",
                    "2. RECONNAISSANCE — quick_web_search + gemini_search to map the landscape",
                    "3. DISCOVER — web_search with targeted queries informed by recon",
                    "4. DEEP-READ — fetch on the most promising results",
                    "5. EVALUATE — identify gaps, single-source claims, domain concentration",
                    "6. ITERATE — go back to step 3 with better queries, or to step 2 with new angles",
                    "7. TERMINATE — when 3+ independent sources agree, or 2 rounds add nothing new",
                    "",
                    "## Decomposition Strategy",
                    "",
                    "Never search the user's question verbatim. Instead:",
                    "- Extract the core concepts and search each independently",
                    "- Ask definitional queries: 'what is X' + fact-seeking queries: 'X vs Y'",
                    "- Search for opposing views: 'criticism of X', 'alternatives to X'",
                    "- Search for recency: 'X 2025', 'X latest', news-focused queries",
                    "- Target 2-4 sub-queries per round. Too many = shallow results.",
                    "",
                    "Example: user asks 'Should we adopt Rust for our backend?'",
                    "Decomposed queries:",
                    "1. 'Rust backend production case studies'",
                    "2. 'Rust vs Go backend performance benchmarks'",
                    "3. 'Rust learning curve developer productivity'",
                    "4. 'Rust web framework ecosystem 2025'",
                    "",
                    "## Reconnaissance Phase",
                    "",
                    "Before deep search, understand the territory:",
                    "- quick_web_search(objective='map the landscape of X') — returns ranked excerpts",
                    "- gemini_search('what are the key facts about X?') — grounded AI synthesis with citations",
                    "- Note terminology, key players, common framings you didn't know about",
                    "- This phase should take 1-2 calls. Do not skip it.",
                    "",
                    "## Discovery Phase",
                    "",
                    "web_search is the primary deep-discovery tool:",
                    "- Leave rewrite=true for semantic search; set rewrite=false for exact literals",
                    "- provider_count >= 2 is a strong signal — results surfaced by multiple engines",
                    "- Use domain_boost to prefer authoritative domains (e.g., github.com, docs.rs)",
                    "- Use domain_block to exclude noise domains (e.g., pinterest, quora)",
                    "- composio_similarlinks on your best URL finds related pages via neural similarity",
                    "- discover_links on a good landing page reveals link-graph connections",
                    "",
                    "## Deep-Reading Phase",
                    "",
                    "Snippets are teasers, not evidence. Always deep-read the best candidates:",
                    "- fetch accepts one URL or urls for a detailed single/bulk read",
                    "- Set focus_query to bias summaries toward what you care about",
                    "- Check window.has_more — content may be truncated; paginate with char_offset",
                    "- Prefer sources with concrete dates, author names, and reproducible examples",
                    "",
                    "## Evaluation & Iteration",
                    "",
                    "After each round, perform gap analysis:",
                    "- Factual gaps: claims without citations, dates without sources",
                    "- Source gaps: only blogs (no official docs), only one domain type",
                    "- Depth gaps: window.has_more, batch has_more — more content available",
                    "- Perspective gaps: only pro-arguments, no criticism; only US sources",
                    "",
                    "Formulate the next round from what you learned:",
                    "- Initial queries were too broad → add specific terms you discovered",
                    "- Discovered a key paper → search for papers that cite it or respond to it",
                    "- Found an expert name → search for their other work, talks, or critiques",
                    "- Results cluster on one domain → broaden with gemini_search or remove domain_boost",
                    "",
                    "## Termination Criteria",
                    "",
                    "Stop when:",
                    "- 3+ independent, authoritative sources agree on key claims",
                    "- You've represented at least 2 distinct perspectives",
                    "- Your last round added no new information beyond what was already found",
                    "- All major sub-questions from decomposition have been addressed",
                    "",
                    "When terminating, say explicitly: what is well-supported, what is contested,\n"
                    "what remains unknown, and your confidence level.",
                    "",
                    "## Anti-Patterns",
                    "",
                    "- Searching the user's exact question instead of decomposing",
                    "- Calling web_search once and calling it done",
                    "- Trusting snippets without deep-reading the page",
                    "- Not checking publication dates — citing 2019 data in 2026",
                    "- Ignoring provider_count=1 results without cross-verification",
                    "- Skipping reconnaissance — missing the right terminology costs rounds",
                ]
            ),
            role="user",
        )
    ]
