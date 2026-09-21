"""Query rewrite prompt contract for the Parallel Search API.

Owns the four-slot rewrite schema used by ``search.quick.quick_web_search_rewrite``:
the caller's original query, a refined extension, and two decomposed angle
queries. Slot rules encode the Parallel Search API request contract (concise
keyword queries, no operators, key entity in every query). The pipeline
rewrite contract in ``query_rewrite.py`` is untouched by this module.
"""

from __future__ import annotations

from ..contracts.base import StrictBase

__all__ = [
    "PARALLEL_REWRITE_PROMPT_VERSION",
    "PARALLEL_REWRITE_SYSTEM",
    "PARALLEL_REWRITE_USER",
    "ParallelRewrittenQueries",
]

PARALLEL_REWRITE_PROMPT_VERSION = "1"

PARALLEL_REWRITE_SYSTEM = (
    "You are a production query planner for the Parallel Search API. Return exactly "
    "one JSON object with exactly four non-empty string keys: original, refined, "
    "decomposed_1, decomposed_2. Treat the input queries and objective as the only "
    "source of query content; never invent facts, product names, versions, "
    "statistics, or named sources."
)

PARALLEL_REWRITE_USER = """Rewrite the supplied search request into four Parallel Search API queries. You are a query planner, not an answer writer.

<CURRENT_CONTEXT>
Current Year: {current_year}
Input Queries: {input_queries}
Objective (intent context): "{objective}"
</CURRENT_CONTEXT>

<SOURCE_OF_TRUTH>
- Substantive query terms come only from Input Queries and Objective.
- Preserve technical compounds, named entities, products, protocols, APIs, models,
  error tokens, and quoted phrases exactly. Never invent facts, versions,
  benchmarks, or named sources.
- Never use pronouns (it, they, this, that) — always the explicit entity name.
- Do not assume current leadership names, market statistics, or implementations.
  When recency matters, prefer "current" or "{current_year}" over invented names.
</SOURCE_OF_TRUTH>

<PARALLEL_QUERY_RULES>
Parallel Search takes 1-5 concise keyword queries alongside a natural-language objective.
- Each query: roughly 3-6 words, never more than 200 characters.
- Keyword strings only — not sentences, not questions, not instructions.
- No operators: no site:, filetype:, inurl:, intitle:, AND, OR, NOT, or minus-exclusion.
- Quotes only around a multi-word proper name that would otherwise split.
- Include the key entity or topic in every query; vary names, synonyms, or angles
  across queries.
- Append {current_year} only when recency matters for the request.
</PARALLEL_QUERY_RULES>

<SLOT_RULES>
original: the caller's primary query, copied verbatim from Input Queries. When
several input queries are supplied, choose the single most central one; do not
edit it.
refined: an extended, sharpened version of original — the same information need
with added specificity drawn only from the inputs and objective (qualifiers,
synonyms, entity or product names). Stay keyword-style; add no new facts.
decomposed_1: a keyword query covering ONE facet or angle of the topic that
original alone under-specifies, chosen for retrieval yield (for example:
mechanism or how-it-works, comparison or benchmarks, official documentation,
implementation examples, recent developments, root causes).
decomposed_2: a second facet materially different from original, refined, and
decomposed_1. The two decomposed slots must not cover the same facet.
</SLOT_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly the four named keys and no extra keys or prose.
2. Keep every slot non-empty and at most 200 characters.
3. All four slots must be materially different retrieval views; no near-duplicates.
4. Every slot contains the key topic entity.
5. No operator tokens anywhere.
</OUTPUT_CHECKLIST>

{{"original": "<original>", "refined": "<refined>", "decomposed_1": "<decomposed_1>", "decomposed_2": "<decomposed_2>"}}"""


class ParallelRewrittenQueries(StrictBase):
    """Named-slot Parallel rewrite output; each slot degrades independently."""

    original: str
    refined: str
    decomposed_1: str
    decomposed_2: str
