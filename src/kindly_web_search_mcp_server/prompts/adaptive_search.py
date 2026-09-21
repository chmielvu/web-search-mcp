"""Adaptive search prompt contract.

This module OWNS the adaptive wave templates, response schemas, and prompt
version. ``search.adaptive`` consumes them; the decision plumbing uses the
dedicated high-context router (``adaptive_search_llm`` chain) for the two
decision stages and the ``summarization`` chain for final synthesis.

Prompt shape follows ``prompts/query_rewrite.py``: a short system contract, a
user template with labeled evidence blocks, hard rule sections, an output
checklist, and a literal JSON example. Templates are plain ``.format``
templates: the decision templates receive ``{evidence_json}`` plus a compact
``{query_history_json}``; synthesis receives only ``{evidence_json}``. Every
literal brace in the examples is doubled.
"""

from __future__ import annotations

import json
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

ADAPTIVE_SEARCH_PROMPT_VERSION = "4"

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Length caps mirror the prompt checklists: targeted queries stay under 400
# chars, gap/reason phrases stay one short sentence. The schema is the hard
# enforcement; the prompt wording is the instruction.
ShortQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
ShortPhrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class TargetedQuery(StrictBase):
    """One targeted web query and the evidence gap it closes."""

    query: ShortQuery = Field(
        description=(
            "Standalone provider-neutral web query aimed at one unresolved requirement; "
            "preserve exact entities, versions, and technical tokens."
        )
    )
    why: ShortPhrase = Field(
        description="The unresolved requirement and the evidence needed to answer it."
    )


class FollowupBatch(StrictBase):
    """Wave-1 decision output: targeted queries; no finish action exists here."""

    queries: list[TargetedQuery] = Field(
        min_length=1,
        max_length=2,
        description="One or two complementary queries for the highest-value evidence gaps.",
    )


class ContinuationDecision(StrictBase):
    """Wave-2 stopping decision with the final wave's queries when searching."""

    action: Literal["finish", "search"] = Field(
        description=(
            "Finish only when every material research requirement has direct evidence; "
            "otherwise search."
        )
    )
    queries: list[TargetedQuery] = Field(
        max_length=2,
        description="Empty when finishing; otherwise 1-2 queries for uncovered requirements.",
    )
    reason: ShortPhrase = Field(
        description="The decisive coverage finding: what is covered or what remains unresolved."
    )

    @model_validator(mode="after")
    def _check_action_consistency(self) -> ContinuationDecision:
        if self.action == "finish" and self.queries:
            raise ValueError("action 'finish' requires an empty queries list")
        if self.action == "search" and not (1 <= len(self.queries) <= 2):
            raise ValueError("action 'search' requires 1-2 queries")
        return self


class SynthesisDraft(StrictBase):
    """Final synthesis text grounded in the returned citation slate only."""

    text: NonBlank = Field(
        description=(
            "Complete answer to the research goal with inline [cN] citations and explicit "
            "evidence gaps."
        )
    )


FOLLOWUP_SYSTEM = (
    "You are the evidence-gap analyst of a bounded multi-wave web search "
    "operation. Return exactly one JSON object with one key: queries, a list of "
    "1-2 objects each with non-empty string keys query and why. Every query must "
    "introduce a concrete document, source class, mechanism, value, or contrary "
    "claim absent from the exact query history; paraphrases are invalid. You "
    "choose the next targeted retrievals and never answer the request yourself."
)

FOLLOWUP_USER = """Assess the accumulated evidence and generate the next targeted search queries. You are an evidence-gap analyst, not an answer writer.

<EVIDENCE_OBJECT>
The user content below is one JSON object with labeled fields:
- current_date: today's date.
- request: normalized query, seed queries, research goal, initial intent,
  preserved_terms, reranking_instructions, and request_filters
  (temporal window, language, region, undated policy).
- executed_searches: every executed branch query, its target_gap when available,
  and the actual request text each provider received.
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
- Anchor the gap analysis to request.query and request.research_goal. Preserve
  named entities, exact versions, error tokens, products, protocols, and user
  restrictions.
- Retrieved passages are factual evidence. Executed searches, adaptive-round
  reasons, scores, and provider signals are planning metadata, not evidence.
- You may add established technical aliases or synonyms that sharpen retrieval,
  but never introduce unsupported facts, versions, benchmarks, or source claims.
- Request filters bind every query you emit.
</SOURCE_OF_TRUTH>

<DECISION_RULES>
1. Decompose research_goal into its material answer requirements and classify
   each as supported, contradicted, ambiguous, or missing from the evidence.
2. Default to two complementary queries whenever they can seek distinct
   evidence, including implementation versus semantics for the same gap. Use
   one only when every plausible second query would be substantively redundant.
3. Each query must use one unused search move for an unresolved requirement:
   seek an exact primary artifact, narrow to a missing mechanism or value,
   request an authoritative source class, seek contrary evidence, or broaden
   only after a narrow search missed. The query must add a concrete retrieval
   discriminator absent from prior provider requests.
4. Rephrasing, synonym substitution, word reordering, or adding generic terms
   such as official, latest, source, report, or evidence is not a new search
   move. Preserve exact request terms and use concrete vocabulary from the
   evidence only when it changes what documents should be retrieved.
5. why names the unresolved requirement and the evidence needed to resolve it.
6. Never select providers, relax filters, emit an answer, or treat a provider
   failure as evidence about the subject.
</DECISION_RULES>

<QUERY_HISTORY>
The compact JSON array below is the exact no-repeat list of branch queries and
provider request texts already attempted. Use it as planning metadata, not
factual evidence:
{query_history_json}
</QUERY_HISTORY>

<NOVELTY_EXAMPLE>
History: ["Python 3.14 free threading status performance"]
Invalid: "Python 3.14 free-threading performance current status"
Valid exact-artifact move: "PEP 779 acceptance criteria supported platforms"
Valid mechanism move: "CPython free-threaded build C extension compatibility guide"
</NOVELTY_EXAMPLE>

<OUTPUT_CHECKLIST>
1. Return exactly {{"queries": [...]}} and no extra keys or prose.
2. Emit 1-2 standalone provider-neutral queries, each under 400 characters.
3. Use no Boolean operators (AND/OR/NOT) or site:/filetype: operators; quoted
   exact phrases are allowed.
4. Preserve exact entities, versions, and technical tokens from the request.
</OUTPUT_CHECKLIST>

<FINAL_TASK>
Select the highest-value unresolved requirements. For each query, choose and
state through its wording an unused search move that would retrieve a different
class of evidence from every entry in QUERY_HISTORY. Do not emit a paraphrase.
</FINAL_TASK>

{{"queries": [{{"query": "<query for one unresolved requirement>", "why": "<missing requirement and needed evidence>"}}]}}"""


CONTINUATION_SYSTEM = (
    "You are the stopping judge of a bounded multi-wave web search operation. "
    'Return exactly one JSON object with keys: action ("finish" or '
    '"search"), queries (empty when finishing), and reason (one short '
    "sentence). Search queries must introduce a concrete document, source "
    "class, mechanism, value, or contrary claim absent from the exact query "
    "history; paraphrases are invalid. You decide whether the evidence answers "
    "the request and never answer it yourself."
)

CONTINUATION_USER = """Decide whether the accumulated evidence can answer the research goal or whether one final targeted retrieval wave is required. You are a coverage judge, not an answer writer.

<EVIDENCE_OBJECT>
The user content below is one JSON object with labeled fields:
- current_date: today's date.
- request: normalized query, seed queries, research goal, initial intent,
  preserved_terms, reranking_instructions, and request_filters
  (temporal window, language, region, undated policy).
- executed_searches: every executed branch query, its target_gap when available,
  and the actual request text each provider received.
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
- Judge coverage against request.query and every material requirement in
  request.research_goal.
- Retrieved passages are factual evidence. Executed searches, adaptive-round
  reasons, scores, and provider signals are planning metadata, not evidence.
- Request filters bind every query you emit.
</SOURCE_OF_TRUTH>

<DECISION_RULES>
1. A requirement is covered only when ranked_evidence directly supports an
   answer. Related background, mention of a configurable field, or high result
   count is not coverage.
2. Choose finish only when every material requirement is covered. Do not infer
   that a missing fact is unavailable merely because prior searches missed it.
3. Choose search for any answerable missing fact, unresolved contradiction,
   ambiguous version or current-state question, unsupported comparison, or
   absent exact value, mechanism, condition, or precedence rule.
4. When searching, use one unused search move for each uncovered requirement:
   seek an exact primary artifact, narrow to a missing mechanism or value,
   request an authoritative source class, seek contrary evidence, or broaden
   only after a narrow search missed. Each query must add a concrete retrieval
   discriminator absent from prior provider requests.
5. Rephrasing, synonym substitution, word reordering, or adding generic terms
   such as official, latest, source, report, or evidence is not a new search
   move. Preserve exact entities, versions, and technical tokens.
6. Never select providers, relax filters, emit an answer, or compensate for
   provider failures in query text. reason states the decisive coverage finding.
</DECISION_RULES>

<QUERY_HISTORY>
The compact JSON array below is the exact no-repeat list of branch queries and
provider request texts already attempted. Use it as planning metadata, not
factual evidence:
{query_history_json}
</QUERY_HISTORY>

<OUTPUT_CHECKLIST>
1. Return exactly {{"action": ..., "queries": [...], "reason": ...}} and no extra keys or prose.
2. action "finish" requires queries == []; action "search" requires 1-2 queries.
3. Every query is provider-neutral, under 400 characters, materially different
   from executed searches, and contains no Boolean or site:/filetype: operators
   (quoted exact phrases allowed).
</OUTPUT_CHECKLIST>

<NOVELTY_EXAMPLE>
History: ["Python 3.14 free threading status performance"]
Invalid: "Python 3.14 free-threading performance current status"
Valid exact-artifact move: "PEP 779 acceptance criteria supported platforms"
Valid mechanism move: "CPython free-threaded build C extension compatibility guide"
</NOVELTY_EXAMPLE>

<FINAL_TASK>
Finish if all material requirements have direct evidence or no unused search
move remains. Otherwise choose 1-2 uncovered requirements and emit only queries
whose concrete discriminator would retrieve a different class of evidence from
every entry in QUERY_HISTORY. Do not emit a paraphrase.
</FINAL_TASK>

{{"action": "search", "queries": [{{"query": "<query for the uncovered requirement>", "why": "<missing requirement and needed evidence>"}}], "reason": "<decisive uncovered requirement>"}}"""


SYNTHESIS_SYSTEM = (
    "You are the research synthesizer of a web search operation. Return exactly "
    "one JSON object with one key: text, a grounded overview of the supplied "
    "evidence. You never search, and you never add information beyond the "
    "supplied hits."
)

SYNTHESIS_USER = """Write a complete, answer-oriented synthesis of the supplied evidence. You are a synthesizer, not a searcher.

<EVIDENCE_OBJECT>
The user content below is one JSON object:
- request: query, research_goal, reranking_instructions, and request_filters.
- stop_reason: why adaptive retrieval ended.
- adaptive_rounds: the executed wave history and coverage decisions.
- ranked_evidence: the final returned hits, each with rank, citation_id, title,
  url, domain, published date, providers, source_kind, evidence_consensus,
  freshness_signal, final_score, and native passages.

{evidence_json}
</EVIDENCE_OBJECT>

<SOURCE_OF_TRUTH>
- Only ranked_evidence passages may support factual claims. The request,
  stop_reason, adaptive_rounds, ranks, scores, consensus, and freshness fields
  guide relevance, coverage, and confidence but are not source evidence.
- Use no prior model output or unsupported world knowledge. Do not invent facts,
  versions, statistics, causal explanations, comparisons, or recommendations.
- The passages are search snippets and provider excerpts, not full-page reviews;
  state the precise evidence limitation when it blocks a requested conclusion.
</SOURCE_OF_TRUTH>

<WRITING_RULES>
1. Answer request.research_goal directly, using request.query as context. Lead
   with the conclusion the evidence supports, then give the decisive details,
   qualifications, disagreements, and remaining gaps.
2. Synthesize across sources instead of summarizing hits one by one. Weight
   direct, current, independently corroborated evidence more strongly than thin
   or indirect excerpts.
3. Place [cN] immediately after every substantive supported claim, using only
   citation_id values in ranked_evidence. Write multiple citations separately
   as [c1][c2], never as a combined marker such as [c1, c2]. Cite both sides
   of disagreements.
4. If an explicit requirement remains unresolved, name the exact missing value,
   rule, comparison, or evidence rather than using generic uncertainty.
5. Use paragraphs, bullets, or a compact table according to the request. Add no
   preamble and do not merely restate source titles.
</WRITING_RULES>

<OUTPUT_CHECKLIST>
1. Return exactly {{"text": "..."}} and no extra keys or prose.
2. At least one [cN] citation appears for a nonempty evidence set, every [cN]
   occurs in ranked_evidence, and every substantive factual claim is cited.
</OUTPUT_CHECKLIST>

{{"text": "<direct synthesis with claim-level [cN] citations>"}}"""


def _query_history(evidence_json: str) -> str:
    """Return a compact, stable list of every query already sent or planned."""
    try:
        evidence = json.loads(evidence_json)
    except (json.JSONDecodeError, TypeError):
        return "[]"
    if not isinstance(evidence, dict):
        return "[]"

    executed_searches = evidence.get("executed_searches", [])
    if not isinstance(executed_searches, list):
        return "[]"

    history: list[str] = []
    seen: set[str] = set()
    for search in executed_searches:
        if not isinstance(search, dict):
            continue
        candidates = [search.get("query")]
        provider_requests = search.get("provider_requests", {})
        if isinstance(provider_requests, dict):
            candidates.extend(provider_requests.values())
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            query = " ".join(candidate.split())
            identity = query.casefold()
            if query and identity not in seen:
                seen.add(identity)
                history.append(query)
    return json.dumps(history, ensure_ascii=False, separators=(",", ":"))


def build_followup_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the wave-1 targeted-query decision."""
    return [
        {"role": "system", "content": FOLLOWUP_SYSTEM},
        {
            "role": "user",
            "content": FOLLOWUP_USER.format(
                evidence_json=evidence_json,
                query_history_json=_query_history(evidence_json),
            ),
        },
    ]


def build_continuation_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the wave-2 stop/search decision."""
    return [
        {"role": "system", "content": CONTINUATION_SYSTEM},
        {
            "role": "user",
            "content": CONTINUATION_USER.format(
                evidence_json=evidence_json,
                query_history_json=_query_history(evidence_json),
            ),
        },
    ]


def build_synthesis_messages(evidence_json: str) -> list[dict[str, str]]:
    """System + user messages for the final synthesis over the returned slate."""
    return [
        {"role": "system", "content": SYNTHESIS_SYSTEM},
        {"role": "user", "content": SYNTHESIS_USER.format(evidence_json=evidence_json)},
    ]
