"""Query rewrite prompt contract.

This module OWNS the rewrite templates, output schema, and prompt version.
``search.planning`` consumes them; nothing imports private names from
planning anymore.

Intent-specific angle blocks are adapted from:
- ``C:/Users/Jan/Downloads/prompts.py`` ``query_writer_instructions``
  (topic analysis, recency, anti-assumption, few-shot subtopics)
- Kavisho-o/GitRAG ``multi_query.py`` (different vocabulary / aspect / abstraction)
- alexdong/query-reformulation ``_PROMPT-comparison.md`` / ``_PROMPT-expansion.md``
- avnlp/dspy-opt ``SubQuerySignature`` (self-contained 5-12 word sub-queries)
- solankinitish/knowledge-ops ``query_planner.py`` (no pronouns)
- jjpietrak/secondbrain ``WEB_QUERY_REFORMULATION_PROMPT`` (per-engine phrasing)
- dkruyt/WebRAgent ``DECOMPOSITION["web_search"]`` (one aspect per query)
"""

from __future__ import annotations

from ..contracts.base import StrictBase
from ..search.intents import SearchIntent, normalize_intent

__all__ = [
    "REWRITE_PROMPT_VERSION",
    "REWRITE_SYSTEM",
    "REWRITE_USER",
    "REWRITE_INTENT_ANGLES",
    "RewrittenQueries",
    "select_rewrite_prompt",
]

REWRITE_PROMPT_VERSION = "10"

REWRITE_SYSTEM = (
    "You are a production web-search query planner. Return exactly one JSON object "
    "with exactly five non-empty string keys: free, serp1, serp2, semantic_tavily, "
    "semantic_exa. You generate the five retrieval angles. Treat the user query and "
    "enrichment evidence as the source of query content; treat the research goal as "
    "metadata only."
)

# Shared slot syntax + grounding. Angle strategy is injected via {intent_angles}.
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
- HARD INCLUSION: every non-empty Preserve Exactly term must occur literally in
  free, semantic_tavily, and semantic_exa. serp1 and serp2 include only the
  Preserve Exactly terms that belong to that slot's facet.
- HARD GOAL SEPARATION: never copy, paraphrase, append, or reorder a multi-word phrase
  from Research Goal. Restate the request using Query and enrichment terms.
- Preserve technical compounds, named entities, products, protocols, APIs, models,
  error tokens, and quoted phrases exactly. Never invent facts, versions, benchmarks,
  or named sources.
- Generic facet words are allowed only when justified by the stated query intent.
- Never use pronouns (it, they, this, that, he, she) — always the explicit entity name.
</SOURCE_OF_TRUTH>

<TOPIC_ANALYSIS>
Assess the query even if it looks brief:
- Identify the essential information need.
- If it has multiple facets (history, recent developments, technical detail, examples),
  treat the five slots as those facets — not five paraphrases of the same string.
- Each slot must be straightforward enough for a general web search engine.
  Avoid complex Boolean operators.
- Keep each slot around 5-12 key terms, under 400 characters.
- Plain keywords; quotation marks only for a supplied stable technical compound.
</TOPIC_ANALYSIS>

<ANTI_ASSUMPTION>
Do not assume current leadership names, market statistics, or implementations.
Do not include a person's name unless it appears in Query or enrichment evidence.
When recency matters, prefer "current" / "{current_year}" over invented names.
</ANTI_ASSUMPTION>

{intent_angles}

<FREE_QUERY_RULES>
free: one provider-neutral keyword query of 6-14 high-signal words or short phrases.
Use no operators or quotes. Add only grounded retrieval terms and add the year only
when recency matters.
</FREE_QUERY_RULES>

<SERP_QUERY_RULES>
serp1 and serp2 are keyword search strings for Google/Brave/DDG-class engines.
Write each as 4-8 words of technical terms. Not a question. Not an answer.
Do not use site:, filetype:, inurl:, intitle:, OR, AND, or NOT.
Quotes only around a multi-word proper name if the name would otherwise split.
If TIME_SENSITIVITY is recent or current, you MAY append the current year once;
do not invent a year.
serp1 and serp2 must cover different facets of Query.
For a comparison, put option A in serp1 and option B (or A vs B) in serp2.
Preserve Exactly terms belong in the slot whose facet uses them;
do not force every preserved term into both slots.
</SERP_QUERY_RULES>

<TAVILY_QUERY_RULES>
semantic_tavily: one focused agent web-search query covering ONE facet of the intent.
Tavily treats this as a search query, not a long-form prompt. Keep it well under
400 characters. Write a short natural-language question OR a short statement
(official examples: "who is Leo Messi?", "Competitors of company ABC.") — not the
whole Query with a question mark appended.
No site:, filetype:, AND, OR, or NOT. Domain filters are API parameters, not query text.
Quotes only around a proper name that must appear verbatim (Tavily exact-match).
If the query could be answered by reading Query alone, rewrite it to a different
missing facet.
</TAVILY_QUERY_RULES>

<EXA_QUERY_RULES>
semantic_exa: describe the page you want to find, not the question to answer.
Exa is embedding nearest-neighbor search: long, semantically rich grammatical phrases.
Good: "detailed blog post about embeddings and vector search written by a practitioner"
Good: "detailed analysis of transformer architecture innovations"
Bad: a 1-2 word keyword bag, a Boolean string, or restating Query as a question.
Do not use site:, AND, OR, or NOT — they are just words to Exa. Quotes do not force
exact match.
When comparing named entities, describe the comparison document you want
(official statistics, primary sources, benchmarks), not a Google-style vs query.
</EXA_QUERY_RULES>

<TEMPORAL_RULES>
Only when Time Sensitivity is "recent" or "current", append {current_year} to free,
serp1, and serp2 and express recency naturally in the semantic slots. Historical or
non-current requests keep their explicit years and do not receive a new year.
</TEMPORAL_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly the five named keys and no extra keys or prose.
2. Keep every slot non-empty. Preserve Exactly terms must appear in free and both
   semantic slots; serp1/serp2 only need the terms used by that facet.
3. Ensure free, serp1, and serp2 are materially different retrieval views.
4. Reject goal-only wording, copied multi-word goal phrases, invented facts, unsupported
   named sources, whole-query quoting, illegal operators (AND/OR/NOT), and duplicate views.
5. semantic_tavily must not restate Query; semantic_exa must describe a target page,
   not ask the question.
</OUTPUT_CHECKLIST>

{{"free": "<free>", "serp1": "<serp1>", "serp2": "<serp2>", "semantic_tavily": "<semantic_tavily>", "semantic_exa": "<semantic_exa>"}}"""


# ---------------------------------------------------------------------------
# Intent angle blocks. The LLM generates all five slots; these blocks only
# tell it WHICH angles to produce. Few-shots are taken from the source prompts.
# ---------------------------------------------------------------------------

_GENERAL_ANGLES = """<ANGLE_STRATEGY intent="general">
Generate five retrieval angles for the same intent, each using different vocabulary,
focusing on a different aspect, or approaching the concept at a different level of
abstraction (GitRAG multi_query). Break the topic into 2-5 subtopics even if the
user query seems brief (query_writer TOPIC_ANALYSIS). Each slot covers one aspect;
do not emit five near-paraphrases. Prefer short keyword slots over "maximize recall"
operator tricks.

Slot mapping:
- free: overarching keyword coverage (query_writer main_query).
- serp1: foundational / official / definitional aspect.
- serp2: a second distinct aspect (applications, limitations, or adjacent concept).
- semantic_tavily: a focused Tavily agent query (question or short statement) about
  ONE missing facet, never Query restated.
- semantic_exa: describe the page to find with a long grammatical phrase
  (Exa searching.md), not a 1-2 word bag.

Preserve Exactly: listed terms appear in free and both semantic slots; serp1/serp2
only include the terms used by that facet. Illegal: AND, OR, NOT.

Few-shot from query_writer Example 3 (agentic RAG), compiled into the five slots:
Query: "agentic RAG systems architecture benefits implementation"
Preserve Exactly: ["agentic RAG"]
{{
  "free": "agentic RAG systems architecture benefits implementation",
  "serp1": "agentic RAG core components architectural patterns design",
  "serp2": "agentic RAG implementation benefits case studies deployments",
  "semantic_tavily": "how do agentic RAG systems plan multi-hop retrieval",
  "semantic_exa": "detailed analysis of agentic RAG architecture in academic papers and official documentation"
}}

Reject (do not emit):
- "agentic RAG architecture site:arxiv.org OR site:github.com"  (forbidden OR)
- dropping "agentic RAG" from free or either semantic slot
- semantic_tavily equal to the original Query with a question mark
</ANGLE_STRATEGY>"""

_COMPARISON_ANGLES = """<ANGLE_STRATEGY intent="comparison">
Identify the core entities and the shared property, then invert the comparison into
per-entity sub-queries plus one consolidating comparison (alexdong _PROMPT-comparison,
inverted). Each sub-query is self-contained, 5-12 words, preserves dates/entities,
excludes explanatory phrases (dspy-opt SubQuerySignature). Never use pronouns
(knowledge-ops query_planner).

Slot mapping:
- free: both entities + shared property as keywords.
- serp1: entity A + shared property (lexical).
- serp2: entity B + shared property (lexical). If Compared Entities has fewer than
  two items, serp2 is a limitations/migration facet of the same comparison — still
  different from serp1.
- semantic_tavily: the inferred comparison question (comparatives/superlatives).
- semantic_exa: describe the page of authoritative evidence that would decide
  the shared property (official statistics, primary sources).

Few-shot from dspy-opt SubQueryGenerator example, compiled into the five slots:
Query: "Compare the economic impact of renewable energy adoption in Germany vs France since 2020, focusing on job creation and GDP growth"
Compared Entities: ["Germany", "France"]
{{
  "free": "Germany France renewable energy economic impact job creation GDP 2020",
  "serp1": "Germany renewable energy economic impact job creation statistics 2020",
  "serp2": "France renewable energy economic impact job creation statistics 2020",
  "semantic_tavily": "which country saw stronger renewable energy job creation Germany or France since 2020",
  "semantic_exa": "Germany France renewable energy GDP growth comparison official statistics 2020"
}}

Few-shot from alexdong _PROMPT-comparison (Tesla inventions), compiled:
Query: "which invention by Tesla comes earlier? Induction motor? Or the Tesla Coil?"
Compared Entities: ["Induction motor", "Tesla Coil"]
{{
  "free": "Tesla induction motor Tesla Coil invention year",
  "serp1": "Year of Tesla invented the Induction motor",
  "serp2": "Year of Tesla invented the Tesla Coil",
  "semantic_tavily": "which Tesla invention came earlier induction motor or Tesla Coil",
  "semantic_exa": "Nikola Tesla induction motor Tesla Coil invention dates primary sources"
}}
</ANGLE_STRATEGY>"""

_CODING_ANGLES = """<ANGLE_STRATEGY intent="ai_coding_and_infrastructure">
You are a search query rewriter for code and infrastructure retrieval (GitRAG
multi_query). Generate five queries that capture the same intent from different
angles: different vocabulary, different aspects, or different abstraction levels.
Preserve error tokens, package names, APIs, and versions verbatim.

Slot mapping (secondbrain engine rules + GitRAG):
- free: error/library keywords, no operators (forum-style  keyword tokens).
- serp1: exact error or API token as a quoted compound when it is a supplied
  stable technical compound; otherwise lexical library + symptom.
- serp2: cause, initialization order, migration, or version-delta facet.
- semantic_tavily: how-to / fix question a practitioner would type.
- semantic_exa: official docs, GitHub issues, RFCs, or implementation examples.

Few-shot adapted from GitRAG ("same intent, different abstraction") plus
query_writer github_search tool category:
Query: "pydantic v2 field_validator not called"
Preserve Exactly: ["pydantic", "v2", "field_validator"]
{{
  "free": "pydantic v2 field_validator not called",
  "serp1": "\\"field_validator\\" pydantic v2 not called",
  "serp2": "pydantic v2 field_validator mode before after validation",
  "semantic_tavily": "why is pydantic v2 field_validator not being called",
  "semantic_exa": "pydantic v2 field_validator migration guide official documentation"
}}
</ANGLE_STRATEGY>"""

_NEWS_ANGLES = """<ANGLE_STRATEGY intent="news">
Recency-sensitive planning from query_writer RECENCY_SENSITIVITY_FRAMEWORK.
For leadership, market data, or corporate developments: include "current" or
{current_year}; do not invent executive names. Sequential strategy: first
identify current state, then background — map those to different slots.

Slot mapping:
- free: event/entity keywords + recency marker.
- serp1: current-state identification (who/what now).
- serp2: a second news facet (impact, reaction, or timeline) — not a paraphrase.
- semantic_tavily: natural-language current-events question.
- semantic_exa: primary reporting / official statements.

Few-shot from query_writer Example 2 and AVOIDING_ASSUMPTION_EXAMPLES:
Query: "current Country X president"
{{
  "free": "current Country X president {current_year}",
  "serp1": "Country X current president name {current_year}",
  "serp2": "Country X recent presidential election result",
  "semantic_tavily": "who is the current president of Country X",
  "semantic_exa": "Country X official government current head of state"
}}
</ANGLE_STRATEGY>"""

_SOCIAL_ANGLES = """<ANGLE_STRATEGY intent="social_media">
secondbrain forum-engine rule: 2-4 keyword tokens, no question framing, for
lexical slots; community discourse for semantic slots. Match the requested
platform or community when it appears in Query or enrichment.

Slot mapping:
- free: platform/community + topic keywords, no operators.
- serp1: named community + topic keywords when the community appears in Query
  (no site: operator; SERP slots are keyword bags).
- serp2: a different community facet (criticism, how-to, or alternative forum)
  still grounded in Query terms.
- semantic_tavily: how practitioners discuss the topic.
- semantic_exa: high-signal threads, AMA, or official community docs.

Do not invent platform names. If no platform is named, keep slots topic-only.
</ANGLE_STRATEGY>"""

_HUMANITIES_ANGLES = """<ANGLE_STRATEGY intent="digital_humanities">
query_writer academic_search tool category + TOPIC_ANALYSIS facets: period,
corpus, method, discipline. Prefer scholarly noun phrases over blog framing.

Slot mapping:
- free: topic + method/corpus keywords.
- serp1: primary source / corpus facet.
- serp2: method or historiography facet, materially different from serp1.
- semantic_tavily: a research question a scholar would ask.
- semantic_exa: papers, editions, catalogues, or archival finding aids.

Few-shot from query_writer Example 3 academic_search subtopics, compiled:
Query: "agentic RAG strategic retrieval planning multi-hop reasoning"
{{
  "free": "agentic RAG strategic retrieval planning multi-hop reasoning",
  "serp1": "agentic RAG strategic retrieval planning",
  "serp2": "multi-hop reasoning retrieval augmented generation",
  "semantic_tavily": "how does agentic RAG plan multi-hop retrieval",
  "semantic_exa": "agentic RAG multi-hop reasoning academic papers"
}}
</ANGLE_STRATEGY>"""


REWRITE_INTENT_ANGLES: dict[SearchIntent, str] = {
    "general": _GENERAL_ANGLES,
    "comparison": _COMPARISON_ANGLES,
    "ai_coding_and_infrastructure": _CODING_ANGLES,
    "news": _NEWS_ANGLES,
    "social_media": _SOCIAL_ANGLES,
    "digital_humanities": _HUMANITIES_ANGLES,
}


def select_rewrite_prompt(intent: SearchIntent | str | None) -> tuple[str, str]:
    """Return (system, user template) for the classified intent.

    The user template still contains ``.format`` fields for the planner.
    ``{intent_angles}`` is already substituted. Six branches still fire;
    only the angle instructions change.
    """
    key = normalize_intent(str(intent) if intent else None)
    angles = REWRITE_INTENT_ANGLES.get(key, REWRITE_INTENT_ANGLES["general"])
    return REWRITE_SYSTEM, REWRITE_USER.replace("{intent_angles}", angles)


class RewrittenQueries(StrictBase):
    """Named-slot rewrite output; each slot degrades independently."""

    free: str
    serp1: str
    serp2: str
    semantic_tavily: str
    semantic_exa: str
