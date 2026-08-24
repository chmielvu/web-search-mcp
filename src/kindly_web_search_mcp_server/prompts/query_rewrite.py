"""Query rewrite prompt contract.

This module OWNS the rewrite templates, output schema, and prompt version.
``search.planning`` consumes them; nothing imports private names from
planning anymore.
"""

from __future__ import annotations

from ..contracts.base import StrictBase

__all__ = [
    "REWRITE_PROMPT_VERSION",
    "REWRITE_SYSTEM",
    "REWRITE_USER",
    "RewrittenQueries",
]

REWRITE_PROMPT_VERSION = "2"

REWRITE_SYSTEM = (
    "Rewrite the user query into five complementary search queries returned as "
    "named JSON slots: free, serp1, serp2, semantic_tavily, semantic_exa."
)

REWRITE_USER = """You are a search query optimizer that generates five complementary queries for web search, one per named slot.

TASK: Given a user query or input seed queries, a research goal, and enrichment evidence, fill every slot below.

<CURRENT_CONTEXT>
Current Year: {current_year}
Time Sensitivity: {time_sensitivity}
Query: "{query}"
Input Seed Queries: {seed_queries}
Research Goal: "{research_goal}"
</CURRENT_CONTEXT>

<ENRICHMENT_EVIDENCE>
Support Terms: {support_terms}
Autosuggest Suggestions: {suggestions}
Compared Entities: {compared_entities}
Decompose Into Facets: {should_decompose}
Preserve Exactly: {preserved_terms}
</ENRICHMENT_EVIDENCE>

<QUERY_NORMALIZATION>
- Convert questions to effective search terms while preserving the user's intent; organize keyword dumps into coherent searches; remove filler words.
- Preserve technical terms, specific models, brands, products, and quoted phrases exactly as written.
</QUERY_NORMALIZATION>

<FREE_QUERY_RULES>
free: one provider-neutral keyword query. Keep the original meaning and add useful additional keywords and short phrases for each key aspect of the request; aim for about 12 words total. Do not invent entities or facts. No search operators. Add the year only when recency matters.
</FREE_QUERY_RULES>

<SERP_QUERY_RULES>
serp1: one SERP keyword query for a lightweight SERP API (hard limit ~50 words / 400 chars).
serp2: one complementary Google-canonical SERP query. When Decompose Into Facets is true and Compared Entities lists two or more names, build serp2 around those entities as distinct facets (e.g. "A vs B").
Both may use ONLY these operators: "exact phrase", site:, filetype:, ext:, -term.
Forbidden everywhere: intitle:, inbody:, inpage:, lang:, loc:, +term, AND/OR/NOT, and any date inside the query text (freshness is applied through structured parameters elsewhere).
The two SERP queries must target different facets or operator structures.
</SERP_QUERY_RULES>

<TAVILY_QUERY_RULES>
semantic_tavily: one natural-language question that a knowledgeable colleague could answer directly, covering the core intent, important entities, and the research goal. No search operators and no quotes.
</TAVILY_QUERY_RULES>

<EXA_QUERY_RULES>
semantic_exa: one natural-language sentence requesting authoritative evidence: primary documentation, benchmarks, official announcements, reputable comparisons. When Compared Entities lists two or more names, explicitly ask for evidence comparing them. No search operators.
</EXA_QUERY_RULES>

<TEMPORAL_RULES>
Only when Time Sensitivity is "recent" or "current", append {current_year} to free/serp1/serp2 and phrase recency naturally in the two semantic slots. Historical queries keep their explicit years.
</TEMPORAL_RULES>

<INSTRUCTIONS>
1. Fill exactly the five named slots; never invent extra keys; never leave a slot empty.
2. serp1 and serp2 must differ in facet or operator structure.
3. Output JSON only.
</INSTRUCTIONS>

{{"free": "<free>", "serp1": "<serp1>", "serp2": "<serp2>", "semantic_tavily": "<semantic_tavily>", "semantic_exa": "<semantic_exa>"}}"""


class RewrittenQueries(StrictBase):
    """Named-slot rewrite output; each slot degrades independently."""

    free: str
    serp1: str
    serp2: str
    semantic_tavily: str
    semantic_exa: str
