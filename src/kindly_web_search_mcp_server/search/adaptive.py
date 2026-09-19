"""Bounded result-conditioned adaptive search controller.

One live ``SearchRun`` lifecycle: the broad first wave exactly as planned, then
at most two LLM-conditioned targeted waves. After every completed wave the full
accumulated outcome tuple is re-ranked by the existing cumulative ranking, so
the final wave's ranking is the final global ranking. Execution metadata and
the synthesis are attached once, at the end.

LLM plumbing: ``build_adaptive_router()`` (the Gemini-first high-context
``adaptive_search_llm`` chain) for the two decision stages — decision feedback
carries the ranked slate with long passages under a 100k-token budget and must
not share the Groq worker chain's ~7k TPM window. The registered
``summarization`` chain handles final synthesis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import date
from typing import Any

from ..analytics.ids import _canonical_result_id as _cri
from ..inference.chain import get_chain
from ..inference.router import LLMRouter, build_adaptive_router
from ..models import ProviderWarning, SearchStopReason
from ..prompts.adaptive_search import (
    ContinuationDecision,
    FollowupBatch,
    SynthesisDraft,
    TargetedQuery,
    build_continuation_messages,
    build_followup_messages,
    build_synthesis_messages,
)
from ..utils.text_clean import clean_query as normalize_query
from ..utils.url_canonicalize import canonicalize_url
from .contracts import (
    BranchOutcome,
    BranchRole,
    DiagnosticsCollector,
    QueryBranch,
    SearchRun,
)
from .evidence import render_search_hit_text
from .ranking import rank_and_finalize
from .retrieval import retrieve_branches
from .types import AdaptiveRound, ScoredHit, SearchRunResult

LOGGER = logging.getLogger(__name__)

MAX_SEARCH_WAVES = 3  # structural bound: the controller below runs exactly 3 waves, not a loop.
MAX_FOLLOWUP_QUERIES = 2  # defensive slice: FollowupBatch/ContinuationDecision already cap at 2.
_DECISION_TIMEOUT_SECONDS = 60.0
_SYNTHESIS_TIMEOUT_SECONDS = 60.0
# Decision feedback runs on the dedicated ``adaptive_search_llm`` chain
# (Gemini-first, 1M context), not the Groq worker chain whose small fallback
# models cap the whole org at roughly 7k input tokens per minute. Budget:
# up to 100k tokens per decision call — 15 ranked hits x 2000-char passages
# (~7.5k tokens) plus executed searches, signals, and scaffolding, with
# headroom for the wave-3 accumulated slate. ``_build_feedback`` enforces the
# byte cap in ``_truncate_feedback`` before serialization.
_ADAPTIVE_FEEDBACK_BUDGET_CHARS = 400_000
_SYNTHESIS_PASSAGE_CHARS = 4000
_DECISION_PASSAGE_CHARS = 2000
_MAX_OTHER_EVIDENCE = 10
_OTHER_EVIDENCE_PASSAGE_CHARS = 800
_CITATION_PATTERN = re.compile(r"\[c(\d+)\]")


def _followup_provider_cohort(plan_search_branches: tuple[QueryBranch, ...]) -> tuple[str, ...]:
    """Stable first-encounter union of the initial branches' provider names."""
    ordered: list[str] = []
    seen: set[str] = set()
    for branch in plan_search_branches:
        for name in branch.provider_names:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return tuple(ordered)


def _query_key(text: str) -> str:
    """Whitespace-normalized, case-folded duplicate key for query texts."""
    return " ".join(text.split()).casefold()


def normalize_followup_queries(
    proposals: list[TargetedQuery],
    executed_queries: tuple[str, ...],
) -> tuple[TargetedQuery, ...]:
    """Normalize proposals and drop blanks/duplicates against prior executed texts.

    Deduplication keys compare executed branch texts, actual provider request
    texts, and the batch itself, so a proposal that merely repeats an earlier
    search cannot be dispatched again.
    """
    seen: set[str] = {_query_key(text) for text in executed_queries if text}
    survivors: list[TargetedQuery] = []
    for proposal in proposals:
        normalized = normalize_query(proposal.query)
        if not normalized:
            continue
        key = _query_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        survivors.append(TargetedQuery(query=normalized, why=proposal.why))
    return tuple(survivors)


def _build_targeted_branches(
    run: SearchRun,
    provider_names: tuple[str, ...],
    proposals: tuple[TargetedQuery, ...],
) -> tuple[QueryBranch, ...]:
    """One FOLLOWUP branch per surviving proposal, each dispatched to the cohort."""
    return tuple(
        QueryBranch(
            role=BranchRole.FOLLOWUP,
            query=proposal.query,
            provider_names=provider_names,
            why=proposal.why,
            support_terms=(),
            max_results=run.request.num_results,
        )
        for proposal in proposals[:MAX_FOLLOWUP_QUERIES]
    )


def _append_followup_variant_rows(
    run: SearchRun,
    branches: tuple[QueryBranch, ...],
) -> None:
    """Append query-variant rows for follow-up branches with global indices."""

    branch_start = len(run.outcomes)
    for local_index, branch in enumerate(branches):
        global_index = branch_start + local_index
        run.diagnostics.query_variant_rows.append(
            {
                "variant_id": _cri(f"{run.run_key}|variant|{global_index}"),
                "run_key": run.run_key,
                "variant_order": global_index,
                "variant_role": branch.role.value,
                "query_text": branch.query,
                "branch_id": _cri(f"{run.run_key}|{global_index}"),
                "selected": True,
                "executed": bool(branch.provider_names),
                "skip_reason": None if branch.provider_names else "no_assigned_providers",
            }
        )


def _provider_failure_count(outcomes: tuple[BranchOutcome, ...]) -> int:
    """Provider-call rows in this wave that did not complete a usable response."""
    return sum(
        1
        for outcome in outcomes
        for row in outcome.provider_calls
        if row.get("status") not in {"success", "partial"}
    )


def _wave_has_provider_response(outcomes: tuple[BranchOutcome, ...]) -> bool:
    """True when at least one provider call in the wave succeeded or partially succeeded."""
    return any(
        row.get("status") in {"success", "partial"}
        for outcome in outcomes
        for row in outcome.provider_calls
    )


def _wave_request_texts(outcomes: tuple[BranchOutcome, ...]) -> list[str]:
    """Actual provider request texts from one wave, for duplicate suppression."""
    texts: list[str] = []
    for outcome in outcomes:
        for row in outcome.provider_calls:
            value = row.get("request_query")
            if isinstance(value, str) and value:
                texts.append(value)
    return texts


def _canonical_urls(candidates: Sequence[ScoredHit]) -> set[str]:
    return {canonicalize_url(candidate.hit.url) for candidate in candidates if candidate.hit.url}


def _domain_count(candidates: Sequence[ScoredHit]) -> int:
    return len({candidate.hit.domain for candidate in candidates if candidate.hit.domain})


def _ranked_evidence_row(hit: ScoredHit, passage_chars: int) -> dict[str, Any]:
    """One ranked hit with long native passages and pipeline-computed signals.

    ``evidence_consensus`` (provider count), ``freshness_signal``, and rank
    scores let the gap analyst tell a thin-but-broad slate from a converged
    one; the raw cross-encoder score is omitted — an unexplained float the
    model cannot calibrate against.
    """
    return {
        "rank": hit.final_rank,
        "citation_id": hit.citation_id,
        "url": hit.hit.url,
        "title": hit.hit.title,
        "domain": hit.hit.domain,
        "published": hit.hit.published,
        "providers": list(hit.providers or ()),
        "source_kind": hit.hit.source_kind,
        "evidence_consensus": hit.evidence_consensus,
        "freshness_signal": hit.freshness_signal,
        "final_score": hit.final_score,
        "passages": render_search_hit_text(hit.hit, max_chars=passage_chars),
    }


def _other_ranked_evidence(
    dc: DiagnosticsCollector,
    top_urls: set[str],
) -> list[dict[str, Any]]:
    """Ranked hits below the top slate with passages, stage, and scores.

    These near-miss rows are the richest gap signal — candidates the pipeline
    saw but cut — so each carries its overflow stage, native passages, and
    pipeline scores instead of a bare title.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stage, item in dc.overflow_ranked:
        key = canonicalize_url(item.url)
        if not item.url or key in top_urls or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "overflow_stage": stage,
                "url": item.url,
                "title": item.title,
                "domain": item.domain,
                "published": item.published,
                "source_kind": item.source_kind,
                "passages": render_search_hit_text(item, max_chars=_OTHER_EVIDENCE_PASSAGE_CHARS),
            }
        )
        if len(rows) >= _MAX_OTHER_EVIDENCE:
            break
    return rows


def _truncate_feedback(evidence: dict[str, Any]) -> dict[str, Any]:
    """Enforce the byte budget with priority: slate > executed set > signals.

    The 100k-token budget (~400k chars) binds the wave-3 accumulated slate,
    not the typical wave-1 call. Truncation sheds overflow rows first, then
    per-branch provider-request lists, then ranked passages — the ranked
    slate itself is never dropped, only shortened.
    """
    ranked = evidence["ranked_evidence"]
    other = evidence["other_ranked_evidence"]
    while (
        len(json.dumps(evidence, ensure_ascii=False, default=str)) > _ADAPTIVE_FEEDBACK_BUDGET_CHARS
    ):
        if other:
            other.pop()
            continue
        executed = evidence["executed_searches"]
        longest = max(
            range(len(executed)),
            key=lambda i: len(executed[i].get("provider_requests", [])),
            default=None,
        )
        if longest is not None and executed[longest].get("provider_requests"):
            executed[longest]["provider_requests"].pop()
            continue
        longest_passage = max(
            range(len(ranked)),
            key=lambda i: len(ranked[i].get("passages", "")),
            default=None,
        )
        if longest_passage is not None and len(ranked[longest_passage].get("passages", "")) > 500:
            ranked[longest_passage]["passages"] = ranked[longest_passage]["passages"][:500].rstrip()
            continue
        break
    return evidence


def _build_feedback(
    run: SearchRun,
    wave_outcomes: tuple[BranchOutcome, ...],
    *,
    result: SearchRunResult,
    new_url_count: int,
    passage_chars: int,
) -> str:
    """Serialize the labeled evidence object that conditions LLM decisions."""
    plan = run.plan
    request = run.request
    dc = run.diagnostics
    temporal = request.options.temporal
    preserved_terms: list[str] = []
    intent: str | None = dc.intent or None
    if plan is not None:
        preserved_terms.extend(str(term) for term in plan.understanding.preserved_terms)
        preserved_terms.extend(str(entity.text) for entity in plan.understanding.entities)
        intent = intent or str(plan.understanding.intent)
    evidence = {
        "current_date": date.today().isoformat(),
        "request": {
            "query": plan.normalized_query if plan is not None else request.query,
            "seed_queries": list(plan.seed_queries) if plan is not None else [],
            "research_goal": request.research_goal,
            "initial_intent": intent,
            "preserved_terms": preserved_terms,
            "reranking_instructions": request.reranking_instructions,
            "request_filters": {
                "temporal_start": temporal.start if temporal is not None else None,
                "temporal_end": temporal.end if temporal is not None else None,
                "language": request.options.language,
                "region": request.options.region,
                "include_undated": request.include_undated,
            },
        },
        "executed_searches": [
            {
                "branch_role": outcome.branch.role.value,
                "query": outcome.branch.query,
                "provider_requests": sorted(
                    {
                        row["request_query"]
                        for row in outcome.provider_calls
                        if isinstance(row.get("request_query"), str) and row["request_query"]
                    }
                )[:8],
            }
            for outcome in run.outcomes
        ],
        "adaptive_rounds": [asdict(round_record) for round_record in dc.adaptive_rounds],
        "ranked_evidence": [_ranked_evidence_row(hit, passage_chars) for hit in result.hits],
        "other_ranked_evidence": _other_ranked_evidence(
            dc,
            {canonicalize_url(hit.hit.url) for hit in result.hits if hit.hit.url},
        ),
        "provider_signals": {
            "expansions": dc.provider_expansions[:8],
            "query_integrities": [
                {
                    "provider": row.get("provider"),
                    "truncated": row.get("truncated"),
                    "sent_query": str(row.get("sent_query") or "")[:120],
                }
                for row in dc.query_integrities[:8]
            ],
            "wave_provider_calls": [
                {
                    "provider": row.get("provider"),
                    "status": row.get("status"),
                    "error_type": row.get("error_type"),
                }
                for outcome in wave_outcomes
                for row in outcome.provider_calls
            ][:24],
        },
        "pool_counts": {
            "candidate_count": len(dc.merged_candidates),
            "new_url_count": new_url_count,
            "domain_count": _domain_count(dc.merged_candidates),
            "overlap_rate": run.rerank_metadata.get("overlap_rate"),
            "duplicate_lists_dropped": run.rerank_metadata.get("duplicate_lists_dropped"),
            "rerank_provider": run.rerank_metadata.get("reranker_provider"),
            "rerank_model": run.rerank_metadata.get("reranker_model"),
        },
    }
    evidence = _truncate_feedback(evidence)
    feedback_json = json.dumps(evidence, ensure_ascii=False, default=str)
    LOGGER.debug("Adaptive feedback built: %d chars", len(feedback_json))
    return feedback_json


def _synthesis_evidence(result: SearchRunResult) -> dict[str, Any]:
    """Final slate with citation ids and native passages for the synthesis call."""
    return {
        "query": result.query,
        "ranked_evidence": [
            {
                "citation_id": hit.citation_id,
                "title": hit.hit.title,
                "url": hit.hit.url,
                "domain": hit.hit.domain,
                "published": hit.hit.published,
                "providers": list(hit.providers or ()),
                "source_kind": hit.hit.source_kind,
                "passages": render_search_hit_text(hit.hit, max_chars=_SYNTHESIS_PASSAGE_CHARS),
            }
            for hit in result.hits
            if hit.citation_id is not None and hit.hit.url
        ],
    }


def _validate_synthesis_text(text: str, valid_ids: set[str]) -> str:
    """Reject empty syntheses and citation references outside the final slate."""
    if not text.strip():
        raise ValueError("Synthesis model returned empty text.")
    cited = {f"c{int(number)}" for number in _CITATION_PATTERN.findall(text)}
    if valid_ids and not cited:
        raise ValueError("Synthesis contains no [cN] citation references.")
    unknown = sorted(cited - valid_ids)
    if unknown:
        raise ValueError(f"Synthesis cites citation ids absent from the final slate: {unknown}")
    return text


async def propose_followups(run: SearchRun, feedback: str) -> FollowupBatch:
    """Wave-1 decision: targeted gap-closing queries from the adaptive chain.

    Runs on ``build_adaptive_router()`` (Gemini-first, 1M context) so the full
    ranked slate with 2000-char passages fits under the 100k-token budget.
    ``reasoning_effort="low"`` buys gap analysis; the Google adapter ignores
    the knob and the Vercel terminal fallback drops it, so the setting is
    safe across the whole chain.
    """
    generation = await build_adaptive_router().complete_json(
        messages=build_followup_messages(feedback),
        temperature=0.0,
        timeout_seconds=_DECISION_TIMEOUT_SECONDS,
        response_model=FollowupBatch,
        reasoning_effort="low",
        run_key=run.run_key,
        operation="search.adaptive.followup",
    )
    return FollowupBatch.model_validate_json(generation.content)


async def decide_continuation(run: SearchRun, feedback: str) -> ContinuationDecision:
    """Wave-2 decision: finish, or emit the final wave's targeted queries.

    Same high-context chain as the wave-1 proposal; the stopping judgment
    needs the same long-passage slate to tell convergence from thin overlap.
    """
    generation = await build_adaptive_router().complete_json(
        messages=build_continuation_messages(feedback),
        temperature=0.0,
        timeout_seconds=_DECISION_TIMEOUT_SECONDS,
        response_model=ContinuationDecision,
        reasoning_effort="none",
        run_key=run.run_key,
        operation="search.adaptive.decision",
    )
    return ContinuationDecision.model_validate_json(generation.content)


async def synthesize_results(run: SearchRun, result: SearchRunResult) -> str:
    """Final synthesis over the returned slate only; citation refs validated.

    The summarization chain runs on Google genai adapters, whose
    ``response_json_schema`` takes a plain schema dict — not a pydantic model
    class — so the schema is passed as ``model_json_schema()`` and the
    generated JSON is validated with the production schema.
    """
    evidence_json = json.dumps(_synthesis_evidence(result), ensure_ascii=False, default=str)
    generation = await LLMRouter(chain=get_chain("summarization")).complete_json(
        messages=build_synthesis_messages(evidence_json),
        temperature=0.0,
        timeout_seconds=_SYNTHESIS_TIMEOUT_SECONDS,
        response_model=SynthesisDraft.model_json_schema(),
        run_key=run.run_key,
        operation="search.adaptive.synthesis",
    )
    draft = SynthesisDraft.model_validate_json(generation.content)
    # Mirror the _synthesis_evidence filter (citation + URL): the prompt only
    # shows URL-bearing hits, so a URL-less id must fail validation rather
    # than pass as citable.
    valid_ids = {
        hit.citation_id for hit in result.hits if hit.citation_id is not None and hit.hit.url
    }
    return _validate_synthesis_text(draft.text, valid_ids)


def _decision_failed_warning() -> ProviderWarning:
    return ProviderWarning(
        provider="adaptive_search",
        error="Adaptive search decision failed; returning accumulated ranked evidence.",
        error_type="decision_failed",
    )


async def _finalize(
    run: SearchRun,
    result: SearchRunResult,
    *,
    rounds: int,
    stop_reason: SearchStopReason,
    adaptive_warnings: list[ProviderWarning],
) -> SearchRunResult:
    """Attach execution metadata and adaptive warnings; synthesize when grounded."""
    if not result.hits and stop_reason in {
        "sufficient_evidence",
        "max_rounds",
        "no_new_queries",
        "decision_failed",
    }:
        stop_reason = "no_results"
    synthesis: str | None = None
    if result.hits and stop_reason != "retrieval_failure":
        try:
            synthesis = await synthesize_results(run, result)
        except Exception as exc:
            LOGGER.warning("Adaptive search synthesis failed: %s", exc)
            adaptive_warnings.append(
                ProviderWarning(
                    provider="adaptive_search",
                    error="Search synthesis failed; ranked results remain available.",
                    error_type="synthesis_failed",
                )
            )
    return replace(
        result,
        rounds=rounds,
        stop_reason=stop_reason,
        synthesis=synthesis,
        warnings=(*result.warnings, *adaptive_warnings),
    )


async def run_adaptive_search(
    run: SearchRun,
    *,
    embedding_task: asyncio.Task[Sequence[float]] | None,
) -> SearchRunResult:
    """Execute the bounded wave sequence on one live SearchRun.

    Continuation is decided only after wave two; wave three flows directly to
    the final synthesis. Retrieval/ranking failures propagate; only the external
    LLM boundary is caught for the degraded paths.
    """
    plan = run.plan
    if plan is None:
        raise RuntimeError("Search must be planned before adaptive retrieval")
    dc = run.diagnostics
    followup_providers = _followup_provider_cohort(plan.branches)
    executed_texts: list[str] = [plan.normalized_query]
    prior_urls: set[str] = set()
    adaptive_warnings: list[ProviderWarning] = []

    wave_branches = plan.branches
    wave_outcomes = await retrieve_branches(run, wave_branches, embedding_task=embedding_task)
    result = await rank_and_finalize(run, run.outcomes, embedding_task=embedding_task)
    current_urls = _canonical_urls(dc.merged_candidates)
    executed_texts.extend(branch.query for branch in wave_branches)
    executed_texts.extend(_wave_request_texts(wave_outcomes))
    round_1 = AdaptiveRound(
        index=1,
        branch_start=0,
        branch_count=len(wave_branches),
        queries=tuple(branch.query for branch in wave_branches),
        candidate_count=len(dc.merged_candidates),
        new_url_count=len(current_urls - prior_urls),
        domain_count=_domain_count(dc.merged_candidates),
        provider_failure_count=_provider_failure_count(wave_outcomes),
        decision="finish",
        reason="",
    )
    prior_urls = current_urls

    if not _wave_has_provider_response(wave_outcomes) or not any(
        branch.provider_names for branch in wave_branches
    ):
        dc.adaptive_rounds.append(replace(round_1, reason="retrieval_failure"))
        return await _finalize(
            run,
            result,
            rounds=1,
            stop_reason="retrieval_failure",
            adaptive_warnings=adaptive_warnings,
        )

    feedback = _build_feedback(
        run,
        wave_outcomes,
        result=result,
        new_url_count=round_1.new_url_count,
        passage_chars=_DECISION_PASSAGE_CHARS,
    )
    try:
        batch = await propose_followups(run, feedback)
    except Exception as exc:
        LOGGER.warning("Adaptive follow-up decision failed: %s", exc)
        adaptive_warnings.append(_decision_failed_warning())
        dc.adaptive_rounds.append(replace(round_1, decision="finish", reason="decision_failed"))
        return await _finalize(
            run,
            result,
            rounds=1,
            stop_reason="decision_failed",
            adaptive_warnings=adaptive_warnings,
        )
    proposals = normalize_followup_queries(batch.queries, tuple(executed_texts))
    if not proposals:
        dc.adaptive_rounds.append(replace(round_1, decision="finish", reason="no_new_queries"))
        return await _finalize(
            run,
            result,
            rounds=1,
            stop_reason="no_new_queries",
            adaptive_warnings=adaptive_warnings,
        )
    dc.adaptive_rounds.append(
        replace(
            round_1,
            decision="search",
            reason=f"{len(proposals)} targeted follow-up query(ies) from wave-one evidence",
        )
    )

    wave2_branches = _build_targeted_branches(run, followup_providers, proposals)
    _append_followup_variant_rows(run, wave2_branches)
    wave2_outcomes = await retrieve_branches(run, wave2_branches, embedding_task=None)
    result = await rank_and_finalize(run, run.outcomes, embedding_task=embedding_task)
    current_urls = _canonical_urls(dc.merged_candidates)
    executed_texts.extend(branch.query for branch in wave2_branches)
    executed_texts.extend(_wave_request_texts(wave2_outcomes))
    round_2 = AdaptiveRound(
        index=2,
        branch_start=len(run.outcomes) - len(wave2_branches),
        branch_count=len(wave2_branches),
        queries=tuple(branch.query for branch in wave2_branches),
        candidate_count=len(dc.merged_candidates),
        new_url_count=len(current_urls - prior_urls),
        domain_count=_domain_count(dc.merged_candidates),
        provider_failure_count=_provider_failure_count(wave2_outcomes),
        decision="finish",
        reason="",
    )
    prior_urls = current_urls

    if not _wave_has_provider_response(wave2_outcomes):
        dc.adaptive_rounds.append(replace(round_2, reason="retrieval_failure"))
        return await _finalize(
            run,
            result,
            rounds=2,
            stop_reason="retrieval_failure",
            adaptive_warnings=adaptive_warnings,
        )

    feedback = _build_feedback(
        run,
        wave2_outcomes,
        result=result,
        new_url_count=round_2.new_url_count,
        passage_chars=_DECISION_PASSAGE_CHARS,
    )
    try:
        decision = await decide_continuation(run, feedback)
    except Exception as exc:
        LOGGER.warning("Adaptive continuation decision failed: %s", exc)
        adaptive_warnings.append(_decision_failed_warning())
        dc.adaptive_rounds.append(replace(round_2, decision="finish", reason="decision_failed"))
        return await _finalize(
            run,
            result,
            rounds=2,
            stop_reason="decision_failed",
            adaptive_warnings=adaptive_warnings,
        )
    if decision.action == "finish":
        dc.adaptive_rounds.append(replace(round_2, decision="finish", reason=decision.reason))
        return await _finalize(
            run,
            result,
            rounds=2,
            stop_reason="sufficient_evidence",
            adaptive_warnings=adaptive_warnings,
        )
    proposals_3 = normalize_followup_queries(decision.queries, tuple(executed_texts))
    if not proposals_3:
        dc.adaptive_rounds.append(replace(round_2, decision="finish", reason="no_new_queries"))
        return await _finalize(
            run,
            result,
            rounds=2,
            stop_reason="no_new_queries",
            adaptive_warnings=adaptive_warnings,
        )
    dc.adaptive_rounds.append(replace(round_2, decision="search", reason=decision.reason))

    wave3_branches = _build_targeted_branches(run, followup_providers, proposals_3)
    _append_followup_variant_rows(run, wave3_branches)
    wave3_outcomes = await retrieve_branches(run, wave3_branches, embedding_task=None)
    result = await rank_and_finalize(run, run.outcomes, embedding_task=embedding_task)
    round_3 = AdaptiveRound(
        index=3,
        branch_start=len(run.outcomes) - len(wave3_branches),
        branch_count=len(wave3_branches),
        queries=tuple(branch.query for branch in wave3_branches),
        candidate_count=len(dc.merged_candidates),
        new_url_count=len(_canonical_urls(dc.merged_candidates) - prior_urls),
        domain_count=_domain_count(dc.merged_candidates),
        provider_failure_count=_provider_failure_count(wave3_outcomes),
        decision="finish",
        reason="",
    )
    if not _wave_has_provider_response(wave3_outcomes):
        dc.adaptive_rounds.append(replace(round_3, reason="retrieval_failure"))
        return await _finalize(
            run,
            result,
            rounds=3,
            stop_reason="retrieval_failure",
            adaptive_warnings=adaptive_warnings,
        )
    dc.adaptive_rounds.append(round_3)
    return await _finalize(
        run,
        result,
        rounds=3,
        stop_reason="max_rounds",
        adaptive_warnings=adaptive_warnings,
    )
