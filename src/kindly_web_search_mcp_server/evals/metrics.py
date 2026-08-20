"""Deterministic eval metrics for tool routing and ranked candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any
from urllib.parse import urlparse

from ..utils.url_canonicalize import canonicalize_url, extract_domain_from_url


def expected_tool_called(tool_calls: list[dict[str, object]], tool_name: str) -> float:
    return 1.0 if _tool_called(tool_calls, tool_name) else 0.0


def forbidden_tool_not_called(tool_calls: list[dict[str, object]], tool_name: str) -> float:
    return 0.0 if _tool_called(tool_calls, tool_name) else 1.0


def latency_within_budget(latency_ms: float, budget_ms: float) -> float:
    return 1.0 if latency_ms <= budget_ms else 0.0


def _tool_called(tool_calls: list[dict[str, object]], tool_name: str) -> bool:
    return any(call.get("tool_name") == tool_name for call in tool_calls)


def _candidate_domain_matches(candidate: dict[str, object], expected_domain: str) -> bool:
    domain = candidate.get("domain")
    if isinstance(domain, str) and domain == expected_domain:
        return True

    url = candidate.get("url")
    if not isinstance(url, str):
        return False
    return urlparse(url).netloc == expected_domain


def _binary_relevance(candidate: dict[str, object]) -> float:
    relevance = candidate.get("relevance", candidate.get("score", 0))
    if isinstance(relevance, bool):
        return 1.0 if relevance else 0.0
    if isinstance(relevance, int | float):
        return 1.0 if relevance > 0 else 0.0
    return 0.0


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 1) for index, gain in enumerate(gains, start=1))


def _url_matches(ranked_url: str, gold: str) -> bool:
    """Exact match after canonicalization, with a domain fallback for bare hosts."""
    ranked_canon = canonicalize_url(ranked_url)
    gold_canon = canonicalize_url(gold)
    if ranked_canon == gold_canon:
        return True
    ranked_domain = extract_domain_from_url(ranked_url)
    gold_domain = extract_domain_from_url(gold)
    return bool(ranked_domain and gold_domain and ranked_domain == gold_domain)


def mrr_at_k(
    candidates: list[dict[str, object]] | list[str],
    gold: list[str],
    k: int = 5,
) -> float:
    """MRR@K using link-list form. Gold is list of canonical URLs or domains."""
    if isinstance(gold, str):
        gold = [gold]
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(str(c.get("link") or c.get("url") or ""))
            else:
                ranked.append(str(c))
    for index, url in enumerate(ranked[:k], start=1):
        if any(_url_matches(url, g) for g in gold):
            return 1.0 / index
    return 0.0


def ndcg_at_k(
    candidates: list[dict[str, object]] | list[str],
    gold: list[str],
    k: int = 10,
) -> float:
    """NDCG@K using link-list form. Gold is list of canonical URLs or domains."""
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(str(c.get("link") or c.get("url") or ""))
            else:
                ranked.append(str(c))
    gains = [1.0 if any(_url_matches(u, g) for g in gold) else 0.0 for u in ranked[:k]]
    ideal = sorted(gains, reverse=True)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(gains) / ideal_dcg


def graded_ndcg_at_k(
    ranked_ids: list[str],
    grades: Mapping[str, int | float],
    k: int = 10,
) -> float:
    """NDCG@K for human-assigned graded relevance judgments."""
    if k < 1:
        raise ValueError("k must be at least 1")
    gains = [max(0.0, float(grades.get(item_id, 0))) for item_id in ranked_ids[:k]]
    ideal_gains = sorted(
        (max(0.0, float(value)) for value in grades.values()),
        reverse=True,
    )[:k]
    ideal_dcg = _dcg(ideal_gains)
    return _dcg(gains) / ideal_dcg if ideal_dcg else 0.0


def top_k_domain_hit(
    candidates: list[dict[str, object]] | list[str],
    gold: list[str] | str,
    k: int = 5,
) -> float:
    if isinstance(gold, str) and candidates and isinstance(candidates[0], dict):
        return (
            1.0
            if any(
                _candidate_domain_matches(candidate, gold)  # type: ignore[arg-type]
                for candidate in candidates[:k]
            )
            else 0.0
        )
    if isinstance(gold, str):
        gold = [gold]
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(str(c.get("link") or c.get("url") or ""))
            else:
                ranked.append(str(c))
    for u in ranked[:k]:
        if any(_url_matches(u, g) for g in gold):
            return 1.0
    return 0.0


def provider_survival_rate(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, min(1.0, after / before))


def duplicate_url_rate(candidates: list[dict[str, object]] | list[str]) -> float:
    urls: list[str] = []
    for c in candidates or []:
        if isinstance(c, str):
            urls.append(c)
        elif isinstance(c, dict):
            urls.append(str(c.get("link") or c.get("url") or ""))
    if not urls:
        return 0.0
    unique = len(set(urls))
    return 1.0 - (unique / len(urls))


def candidate_count_delta(before: int, after: int) -> int:
    return before - after


# ============================================================================
# Code Search Agent-Ready Evidence Rate Metrics
# ============================================================================

_ALLOWED_RESULT_KINDS = {
    "code_match",
    "code",
    "documentation",
    "doc",
    "docs",
    "implementation",
    "source",
}

_DISALLOWED_RESULT_KINDS = {
    "semantic_page",
    "semantic",
    "repository",
    "repo",
    "issue",
    "pr",
    "commit",
    "unknown",
}

_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _get_val(cand: Mapping[str, Any] | object, key: str, default: Any = None) -> Any:
    if isinstance(cand, Mapping):
        return cand.get(key, default)
    return getattr(cand, key, default)


def assess_candidate_readiness(candidate: Mapping[str, Any] | object) -> tuple[bool, list[str]]:
    """Assess whether a code-search candidate dictionary is agent-ready.

    A candidate is ready when:
    1. It is a code or documentation result kind (not semantic-only or repository-only).
    2. It has a non-empty canonical URL.
    3. It has sufficient text context (hydrated source, text fragments, or snippet).
    4. It has exact line coordinates OR an immutable source revision.

    Returns:
        tuple[bool, list[str]]: (is_ready, fail_reasons)
    """
    if candidate is None:
        return False, ["null_candidate"]

    fail_reasons: list[str] = []

    # 1. Result Kind Check
    result_kind_raw = _get_val(candidate, "result_kind") or _get_val(candidate, "kind")
    if result_kind_raw is not None:
        result_kind = str(result_kind_raw).strip().casefold()
        if result_kind in _DISALLOWED_RESULT_KINDS or result_kind not in _ALLOWED_RESULT_KINDS:
            fail_reasons.append("non_evidence_result_kind")
    else:
        # If result_kind is omitted, check if it's explicitly repository-only
        if _get_val(candidate, "repository") and not (
            _get_val(candidate, "path")
            or _get_val(candidate, "line_start")
            or _get_val(candidate, "hydrated_source")
            or _get_val(candidate, "fragments")
        ):
            fail_reasons.append("non_evidence_result_kind")

    # 2. URL Check
    url = _get_val(candidate, "url") or _get_val(candidate, "link")
    if not url:
        location = _get_val(candidate, "location")
        if location:
            url = _get_val(location, "url")
    if not url or not isinstance(url, str) or not url.strip():
        fail_reasons.append("missing_url")

    # 3. Sufficient Text Context Check
    has_text_context = False
    hydrated_source = _get_val(candidate, "hydrated_source")
    if isinstance(hydrated_source, str) and hydrated_source.strip():
        has_text_context = True
    else:
        fragments = _get_val(candidate, "fragments")
        if isinstance(fragments, list) and len(fragments) > 0:
            for frag in fragments:
                frag_text = _get_val(frag, "text") if not isinstance(frag, str) else frag
                if isinstance(frag_text, str) and frag_text.strip():
                    has_text_context = True
                    break
        if not has_text_context:
            snippet = (
                _get_val(candidate, "snippet")
                or _get_val(candidate, "text")
                or _get_val(candidate, "content")
                or _get_val(candidate, "source")
                or _get_val(candidate, "body")
            )
            if isinstance(snippet, str) and snippet.strip():
                has_text_context = True

    if not has_text_context:
        fail_reasons.append("insufficient_text_context")

    # 4. Line Coordinates OR Immutable Revision Check
    has_lines = False
    line_start = _get_val(candidate, "line_start")
    lines_avail = _get_val(candidate, "lines_available")
    location = _get_val(candidate, "location")

    if location:
        if line_start is None:
            line_start = _get_val(location, "line_start")
        if lines_avail is None:
            lines_avail = _get_val(location, "lines_available")

    if (isinstance(line_start, int) and line_start >= 1) or bool(lines_avail):
        has_lines = True
    elif line_start is None:
        # Check first fragment line coordinates
        fragments = _get_val(candidate, "fragments")
        if isinstance(fragments, list) and fragments:
            frag_line = _get_val(fragments[0], "line_start")
            if isinstance(frag_line, int) and frag_line >= 1:
                has_lines = True

    has_revision = False
    revision = (
        _get_val(candidate, "revision")
        or _get_val(candidate, "commit_oid")
        or _get_val(candidate, "sha")
    )
    rev_avail = _get_val(candidate, "revision_available")

    if location:
        if not revision:
            revision = (
                _get_val(location, "revision")
                or _get_val(location, "commit_oid")
                or _get_val(location, "sha")
            )
        if rev_avail is None:
            rev_avail = _get_val(location, "revision_available")

    if bool(rev_avail) or (
        isinstance(revision, str) and bool(_IMMUTABLE_REVISION.fullmatch(revision.strip()))
    ):
        has_revision = True

    if not (has_lines or has_revision):
        fail_reasons.append("missing_lines_or_revision")

    is_ready = len(fail_reasons) == 0
    return is_ready, fail_reasons


def is_candidate_agent_ready(candidate: Mapping[str, Any] | object) -> bool:
    """Return True if candidate satisfies all Agent-Ready requirements."""
    return assess_candidate_readiness(candidate)[0]


def candidate_failure_reasons(candidate: Mapping[str, Any] | object) -> list[str]:
    """Return list of failure reasons explaining why candidate is not agent-ready."""
    return assess_candidate_readiness(candidate)[1]


def agent_ready_evidence_rate(
    candidates: Sequence[Mapping[str, Any] | object] | None,
) -> float:
    """Deterministic Agent-Ready Evidence Rate over code-search candidates.

    Returns the proportion (0.0 to 1.0) of candidates that satisfy all
    evidence readiness constraints. Returns 0.0 for empty candidate sets.
    """
    if not candidates:
        return 0.0
    ready_count = sum(1 for c in candidates if assess_candidate_readiness(c)[0])
    return ready_count / len(candidates)


# Canonical alias for agent_ready_evidence_rate
evidence_rate = agent_ready_evidence_rate


def agent_ready_breakdown(
    candidates: Sequence[Mapping[str, Any] | object] | None,
) -> dict[str, Any]:
    """Provide a detailed breakdown of agent-readiness across candidates."""
    if not candidates:
        return {
            "total": 0,
            "ready_count": 0,
            "evidence_rate": 0.0,
            "ready_indices": [],
            "failures": [],
        }

    ready_indices: list[int] = []
    failures: list[dict[str, Any]] = []

    for idx, c in enumerate(candidates):
        ready, reasons = assess_candidate_readiness(c)
        if ready:
            ready_indices.append(idx)
        else:
            failures.append({
                "index": idx,
                "candidate": c,
                "reasons": reasons,
            })

    total = len(candidates)
    ready_count = len(ready_indices)
    rate = ready_count / total if total > 0 else 0.0

    return {
        "total": total,
        "ready_count": ready_count,
        "evidence_rate": rate,
        "ready_indices": ready_indices,
        "failures": failures,
    }


def line_precision_rate(
    candidates: Sequence[Mapping[str, Any] | object] | None,
) -> float:
    """Fraction of candidates with exact source line coordinates."""
    if not candidates:
        return 0.0
    count = 0
    for c in candidates:
        line_start = _get_val(c, "line_start")
        lines_avail = _get_val(c, "lines_available")
        location = _get_val(c, "location")
        if location:
            if line_start is None:
                line_start = _get_val(location, "line_start")
            if not lines_avail:
                lines_avail = _get_val(location, "lines_available")
        if (isinstance(line_start, int) and line_start >= 1) or bool(lines_avail):
            count += 1
    return count / len(candidates)


def match_data_rate(
    candidates: Sequence[Mapping[str, Any] | object] | None,
) -> float:
    """Fraction of candidates with exact match data or bounded fragments."""
    if not candidates:
        return 0.0
    count = 0
    for c in candidates:
        match_avail = _get_val(c, "match_data_available")
        fragments = _get_val(c, "fragments")
        match_spans = _get_val(c, "match_spans")
        location = _get_val(c, "location")
        if location and not match_avail:
            match_avail = _get_val(location, "match_data_available")
        if bool(match_avail) or bool(fragments) or bool(match_spans):
            count += 1
    return count / len(candidates)
