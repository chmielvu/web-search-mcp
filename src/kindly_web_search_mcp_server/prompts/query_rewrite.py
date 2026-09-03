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

REWRITE_PROMPT_VERSION = "7"

REWRITE_SYSTEM = (
    "You are a production web-search query planner. Return exactly one JSON object "
    "with exactly five non-empty string keys: free, serp1, serp2, semantic_tavily, "
    "semantic_exa. Treat the user query and enrichment evidence as the source of "
    "query content; treat the research goal as metadata only."
)

REWRITE_USER = """Create five complementary search queries from the supplied request. You are a query planner, not an answer writer.

<CURRENT_CONTEXT>
Current Year: {current_year}
Time Sensitivity: {time_sensitivity}
Query: "{query}"
Input Seed Queries: {seed_queries}
Research Goal (metadata only): "{research_goal}"
</CURRENT_CONTEXT>

<ENRICHMENT_EVIDENCE>
Support Terms: {support_terms}
Autosuggest Suggestions: {suggestions}
Compared Entities: {compared_entities}
Decompose Into Facets: {should_decompose}
Preserve Exactly: {preserved_terms}
</ENRICHMENT_EVIDENCE>

<SOURCE_OF_TRUTH>
- Substantive query terms come only from Query, Input Seed Queries, Support Terms,
  Autosuggest Suggestions, Compared Entities, or Preserve Exactly.
- HARD INCLUSION: every non-empty Preserve Exactly term must occur literally in every
  slot. Rewrite any slot that is missing an item before emitting JSON.
- HARD GOAL SEPARATION: never copy, paraphrase, append, or reorder a multi-word phrase
  from Research Goal. Restate the request using Query and enrichment terms.
- Preserve technical compounds, named entities, products, protocols, APIs, models,
  error tokens, and quoted phrases exactly. Never invent facts, versions, benchmarks,
  or named sources. A site: qualifier may use a canonical domain for a named project
  or standard, but must not invent an organization.
- Generic facet words are allowed only when justified by the stated query intent.
</SOURCE_OF_TRUTH>

<QUERY_NORMALIZATION>
- Convert questions into concise high-signal search terms without changing intent.
- Keep every supplied technical anchor in every slot; remove filler words only.
- Make the five slots complementary retrieval views, not superficial rewrites.
</QUERY_NORMALIZATION>

<FREE_QUERY_RULES>
free: one provider-neutral keyword query of 6-14 high-signal words or short phrases.
Use no operators or quotes. Add only grounded retrieval terms and add the year only
when recency matters.
</FREE_QUERY_RULES>

<SERP_QUERY_RULES>
serp1: one concise lexical SERP query for broad or official retrieval.
serp2: one materially different SERP query for comparison, implementation, diagnostics,
limitations, migration, or failure. Use Compared Entities as distinct facets when
Decompose Into Facets is true.
Use terms, not a sentence. Never quote the entire user query. Quote a supplied stable
technical compound when useful, or use site:, filetype:, ext:, or -term.
Allowed operators are ONLY exact phrase quotes, site:, filetype:, ext:, and -term.
Forbidden operators: intitle:, inbody:, inpage:, lang:, loc:, +term, AND, OR, NOT.
Do not put dates in SERP text. free, serp1, and serp2 must be materially different;
neither SERP slot may repeat free verbatim.
</SERP_QUERY_RULES>

<TAVILY_QUERY_RULES>
semantic_tavily: one natural-language question covering the original intent and
important entities. No operators, quotes, or unsupported claims.
</TAVILY_QUERY_RULES>

<EXA_QUERY_RULES>
semantic_exa: one natural-language sentence requesting authoritative documentation,
evidence, benchmarks, or code examples relevant to the original query. Compare named
entities when the query compares them. No operators.
</EXA_QUERY_RULES>

<TEMPORAL_RULES>
Only when Time Sensitivity is "recent" or "current", append {current_year} to free,
serp1, and serp2 and express recency naturally in the semantic slots. Historical or
non-current requests keep their explicit years and do not receive a new year.
</TEMPORAL_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly the five named keys and no extra keys or prose.
2. Keep every slot non-empty and include every non-empty Preserve Exactly term literally.
3. Ensure free, serp1, and serp2 are materially different retrieval views.
4. Reject goal-only wording, copied multi-word goal phrases, invented facts, unsupported
   named sources, whole-query quoting, illegal operators, and duplicate views.
</OUTPUT_CHECKLIST>

{{"free": "<free>", "serp1": "<serp1>", "serp2": "<serp2>", "semantic_tavily": "<semantic_tavily>", "semantic_exa": "<semantic_exa>"}}"""


class RewrittenQueries(StrictBase):
    """Named-slot rewrite output; each slot degrades independently."""

    free: str
    serp1: str
    serp2: str
    semantic_tavily: str
    semantic_exa: str
