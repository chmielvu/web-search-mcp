"""Adaptive search prompt contract.

This module OWNS the adaptive wave templates, response schemas, and prompt
version. ``search.adaptive`` consumes them; the decision plumbing uses the
dedicated high-context router (``adaptive_search_llm`` chain) for the two
decision stages and the ``summarization`` chain for final synthesis.

Prompt shape follows ``prompts/query_rewrite.py``: a short system contract, a
user template with labeled evidence blocks, hard rule sections, an output
checklist, and a literal JSON example. Templates are plain ``.format``
templates: ``{evidence_json}`` is the single runtime field and every literal
brace in the examples is doubled.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ..contracts.base import StrictBase

__all__ = [
    "ADAPTIVE_SEARCH_PROMPT_VERSION",
    "ContinuationDecision",
    "FollowupBatch",
    "SynthesisDraft",
    "TargetedQuery",
    "build_continuation_messages",
    "build_followup_messages",
    "build_synthesis_messages",
]

ADAPTIVE_SEARCH_PROMPT_VERSION = "2"

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Length caps mirror the prompt checklists: targeted queries stay under 400
# chars, gap/reason phrases stay one short sentence. The schema is the hard
# enforcement; the prompt wording is the instruction.
ShortQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
ShortPhrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class TargetedQuery(StrictBase):
    """One targeted web query and the evidence gap it closes."""

    query: ShortQuery
    why: ShortPhrase


class FollowupBatch(StrictBase):
    """Wave-1 decision output: targeted queries; no finish action exists here."""

    queries: list[TargetedQuery] = Field(min_length=1, max_length=2)


class ContinuationDecision(StrictBase):
    """Wave-2 stopping decision with the final wave's queries when searching."""

    action: Literal["finish", "search"]
    queries: list[TargetedQuery] = Field(max_length=2)
    reason: ShortPhrase

    @model_validator(mode="after")
    def _check_action_consistency(self) -> ContinuationDecision:
        if self.action == "finish" and self.queries:
            raise ValueError("action 'finish' requires an empty queries list")
        if self.action == "search" and not (1 <= len(self.queries) <= 2):
            raise ValueError("action 'search' requires 1-2 queries")
        return self


class SynthesisDraft(StrictBase):
    """Final synthesis text grounded in the returned citation slate only."""

    text: NonBlank


FOLLOWUP_SYSTEM = (
    "You are the evidence-gap analyst of a bounded multi-wave web search "
    "operation. Return exactly one JSON object with one key: queries, a list of "
    "1-2 objects each with non-empty string keys query and why. You choose the "
    "next targeted retrievals; you never answer the request yourself."
)

FOLLOWUP_USER = """Assess the retrieved evidence and generate the next targeted search queries. You are an evidence-gap analyst, not an answer writer.

<EVIDENCE_OBJECT>
The user content below is one JSON object with labeled fields:
- current_date: today's date.
- request: normalized query, seed queries, research goal, initial intent,
  preserved_terms, reranking_instructions, and request_filters
  (temporal window, language, region, undated policy).
- executed_searches: every executed branch query with the actual request text
  each provider received.
- ranked_evidence: the current top-ranked hits with citation_id, long native
  passages (up to ~2000 chars each), evidence_consensus (provider count),
  freshness_signal, and final_score.
- other_ranked_evidence: up to ten additional ranked hits below the top slate,
  each with its overflow_stage, native passages, and source metadata.
- provider_signals: expansions, query_integrities, and wave_provider_calls
  (current-wave provider call statuses).
- pool_counts: cumulative filtered candidates, new canonical URLs versus the
  previous pool, unique domains, overlap_rate, duplicate_lists_dropped, and
  the rerank provider/model.

{evidence_json}
</EVIDENCE_OBJECT>

<SOURCE_OF_TRUTH>
- Anchor every judgment to the request: query and research_goal. Keep named
  entities, exact technical terms, error tokens, products, protocols, and user
  restrictions intact.
- Substantive query terms come only from the request, the executed searches, and
  the retrieved evidence. Never invent versions, benchmarks, statistics, or
  named sources.
- The retrieved snippets and provider excerpts are the only evidence. Prior
  adaptive_rounds reasons are planning context, never source evidence.
- Request filters (dates, locale, undated policy) bind every query you emit.
</SOURCE_OF_TRUTH>

<DECISION_RULES>
1. Identify a missing facet, contradiction, ambiguity, or verification need that
   the current evidence leaves open. If the evidence looks broad but thin,
   target the concrete gap rather than broadening the query.
2. Generate 1-2 standalone targeted web queries that close that gap using
   concrete terminology discovered in the evidence. Never reissue a query equal
   to any executed search.
3. why names the evidence gap in one short sentence, referencing what the
   evidence showed or failed to show.
4. Never select providers, never relax filters, never emit a synthesis or
   answer, and never explain reasoning beyond the why fields.
5. A provider failure is infrastructure, not evidence that the query needs
   changing; do not route around it in query text.
</DECISION_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly {{"queries": [...]}} and no extra keys or prose.
2. 1-2 standalone keyword-oriented queries, each under 400 characters.
3. No Boolean operators (AND/OR/NOT) and no site:/filetype: operators.
   Quoted exact phrases ("like this") are allowed for targeted gap-closing.
4. Each query differs materially from every executed search above.
</OUTPUT_CHECKLIST>

{{"queries": [{{"query": "<targeted query>", "why": "<evidence gap>"}}]}}"""


CONTINUATION_SYSTEM = (
    "You are the stopping judge of a bounded multi-wave web search operation. "
    'Return exactly one JSON object with keys: action ("finish" or '
    '"search"), queries (empty when finishing), and reason (one short '
    "sentence). You decide whether the evidence answers the request; you never "
    "answer it yourself."
)

CONTINUATION_USER = """Decide whether one more targeted retrieval wave is justified, or whether the accumulated evidence should be finalized. You are a stopping judge, not an answer writer.

<EVIDENCE_OBJECT>
The user content below is one JSON object with labeled fields:
- current_date: today's date.
- request: normalized query, seed queries, research goal, initial intent,
  preserved_terms, reranking_instructions, and request_filters
  (temporal window, language, region, undated policy).
- executed_searches: every executed branch query with the actual request text
  each provider received.
- adaptive_rounds: prior wave records (queries, decision, reason, pool stats).
- ranked_evidence: the current top-ranked hits with citation_id, long native
  passages (up to ~2000 chars each), evidence_consensus (provider count),
  freshness_signal, and final_score.
- other_ranked_evidence: up to ten additional ranked hits below the top slate,
  each with its overflow_stage, native passages, and source metadata.
- provider_signals: expansions, query_integrities, and wave_provider_calls
  (current-wave provider call statuses).
- pool_counts: cumulative filtered candidates, new canonical URLs versus the
  previous pool, unique domains, overlap_rate, duplicate_lists_dropped, and
  the rerank provider/model.

{evidence_json}
</EVIDENCE_OBJECT>

<SOURCE_OF_TRUTH>
- Anchor every judgment to the request: query and research_goal.
- The retrieved snippets and provider excerpts are the only evidence. Prior
  adaptive_rounds reasons are planning context, never source evidence.
- Request filters (dates, locale, undated policy) bind every query you emit.
</SOURCE_OF_TRUTH>

<DECISION_RULES>
1. Choose finish when the evidence covers the research goal, or when a further
   search would only repeat the executed searches and return the same material.
   Finishing is a decision to stop, not a claim of completeness: the synthesis
   must still state remaining unknowns.
2. Otherwise choose search and emit 1-2 standalone targeted queries for the
   remaining concrete gaps, using terminology discovered in the evidence. Do not
   paraphrase an executed search.
3. Never select providers, never relax filters, and never emit a synthesis,
   answer, or recommendation in the decision response.
4. A provider failure is infrastructure, not evidence that the query needs
   changing; do not compensate for it in query text.
5. reason is one short operational sentence the caller can surface publicly.
</DECISION_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly {{"action": ..., "queries": [...], "reason": ...}} and no extra keys or prose.
2. action "finish" requires queries == []; action "search" requires 1-2 queries.
3. Every query under 400 characters, standalone, materially different from
   every executed search above, with no Boolean or site:/filetype: operators
   (quoted exact phrases allowed).
</OUTPUT_CHECKLIST>

{{"action": "finish", "queries": [], "reason": "<one sentence>"}}"""


SYNTHESIS_SYSTEM = (
    "You are the research synthesizer of a web search operation. Return exactly "
    "one JSON object with one key: text, a grounded overview of the supplied "
    "evidence. You never search, and you never add information beyond the "
    "supplied hits."
)

SYNTHESIS_USER = """Write one concise, answer-oriented synthesis of the supplied evidence. You are a synthesizer, not a searcher.

<EVIDENCE_OBJECT>
The user content below is one JSON object:
- query: the user's search query.
- ranked_evidence: the final returned hits, each with citation_id, title, url,
  domain, published date, providers, source_kind, and native passages
  (snippets/excerpts, not full page text).

{evidence_json}
</EVIDENCE_OBJECT>

<SOURCE_OF_TRUTH>
- Only the supplied ranked_evidence may be cited or asserted. No claims from
  prior model outputs, no world knowledge, no invented facts, versions, or
  statistics.
- Evidence is search snippets and provider excerpts, not a full-page review;
  state that limitation when the answer depends on details beyond the snippets.
</SOURCE_OF_TRUTH>

<WRITING_RULES>
1. Answer the query directly and concisely, ordered by what the user needs to
   know, not by citation order.
2. Cite inline with [cN] markers using ONLY the citation_id values present in
   ranked_evidence. Every substantive claim traces to a cited item; never cite
   an id absent from the list.
3. State material disagreements between sources explicitly, with both sides
   cited.
4. State remaining unknowns or gaps explicitly; do not imply completeness.
5. No preamble or meta commentary ("here is a summary"), no bullet-list-only
   restating of titles, and no citations of unlisted results.
</WRITING_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly {{"text": "..."}} and no extra keys or prose.
2. At least one [cN] citation appears for a nonempty evidence set, and every
   [cN] occurs in ranked_evidence.
</OUTPUT_CHECKLIST>

{{"text": "<synthesis with [cN] citations>"}}"""


def build_followup_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the wave-1 targeted-query decision."""
    return [
        {"role": "system", "content": FOLLOWUP_SYSTEM},
        {"role": "user", "content": FOLLOWUP_USER.format(evidence_json=evidence_json)},
    ]


def build_continuation_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the wave-2 stop/search decision."""
    return [
        {"role": "system", "content": CONTINUATION_SYSTEM},
        {"role": "user", "content": CONTINUATION_USER.format(evidence_json=evidence_json)},
    ]


def build_synthesis_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the final synthesis over the returned slate."""
    return [
        {"role": "system", "content": SYNTHESIS_SYSTEM},
        {"role": "user", "content": SYNTHESIS_USER.format(evidence_json=evidence_json)},
    ]
