"""Deterministic eval metrics for tool routing and ranked candidates."""

from __future__ import annotations

import math
from urllib.parse import urlparse


def expected_tool_called(tool_calls: list[dict[str, object]], tool_name: str) -> float:
    return 1.0 if _tool_called(tool_calls, tool_name) else 0.0


def forbidden_tool_not_called(
    tool_calls: list[dict[str, object]], tool_name: str
) -> float:
    return 0.0 if _tool_called(tool_calls, tool_name) else 1.0


def latency_within_budget(latency_ms: float, budget_ms: float) -> float:
    return 1.0 if latency_ms <= budget_ms else 0.0


def mrr_at_k(candidates: list[dict[str, object]], expected_domain: str, k: int) -> float:
    for index, candidate in enumerate(candidates[:k], start=1):
        if _candidate_domain_matches(candidate, expected_domain):
            return 1.0 / index
    return 0.0


def ndcg_at_k(candidates: list[dict[str, object]], k: int) -> float:
    gains = [_binary_relevance(candidate) for candidate in candidates[:k]]
    ideal = sorted(gains, reverse=True)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(gains) / ideal_dcg


def top_k_domain_hit(
    candidates: list[dict[str, object]], expected_domain: str, k: int
) -> float:
    return 1.0 if any(
        _candidate_domain_matches(candidate, expected_domain)
        for candidate in candidates[:k]
    ) else 0.0


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


# Rerank-specific metrics (Phase 4). Support both legacy dict form and simple link-list form
# for the deterministic harness. Gold is list of canonical URLs or domains.


def mrr_at_k(
    candidates: list[dict[str, object]] | list[str],
    gold: list[str] | str,
    k: int = 5,
) -> float:
    """MRR@K supporting both legacy (candidates dicts + expected_domain str) and link-list form."""
    # legacy path: second arg is domain str, candidates are dicts
    if isinstance(gold, str) and candidates and isinstance(candidates[0], dict):
        # fall back to original impl for domain
        for index, candidate in enumerate(candidates[:k], start=1):
            if _candidate_domain_matches(candidate, gold):
                return 1.0 / index
        return 0.0
    if isinstance(gold, str):
        gold = [gold]
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(c.get("link") or c.get("url") or "")
            else:
                ranked.append(str(c))
    for index, url in enumerate(ranked[:k], start=1):
        if any(g in url or url in g for g in gold):
            return 1.0 / index
    return 0.0


def ndcg_at_k(
    candidates: list[dict[str, object]] | list[str],
    gold_or_k: list[str] | str | int = 10,
    k: int = 10,
) -> float:
    """Supports legacy ndcg_at_k(cands, k) and ndcg_at_k(cands, gold, k)."""
    if isinstance(gold_or_k, int):
        # legacy 2-arg: cands, k ; use internal relevance
        k = gold_or_k
        gains = [_binary_relevance(c) for c in (candidates or [])[:k]]
        ideal = sorted(gains, reverse=True)
        ideal_dcg = _dcg(ideal)
        if ideal_dcg == 0:
            return 0.0
        return _dcg(gains) / ideal_dcg
    gold: list[str] = [gold_or_k] if isinstance(gold_or_k, str) else list(gold_or_k or [])
    if isinstance(gold[0] if gold else None, str) and candidates and isinstance(candidates[0] if candidates else None, dict):
        # treat gold as domain for legacy dicts too
        # but for rerank gold is urls, fall to link match below
        pass
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(c.get("link") or c.get("url") or "")
            else:
                ranked.append(str(c))
    gains = [1.0 if any(g in u or u in g for g in gold) else 0.0 for u in ranked[:k]]
    ideal = sorted(gains, reverse=True)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(gains) / ideal_dcg


def top_k_domain_hit(
    candidates: list[dict[str, object]] | list[str],
    gold: list[str] | str,
    k: int = 5,
) -> float:
    if isinstance(gold, str) and candidates and isinstance(candidates[0], dict):
        return 1.0 if any(
            _candidate_domain_matches(candidate, gold)
            for candidate in candidates[:k]
        ) else 0.0
    if isinstance(gold, str):
        gold = [gold]
    ranked: list[str] = []
    if candidates and isinstance(candidates[0], str):
        ranked = list(candidates)  # type: ignore[assignment]
    else:
        for c in candidates or []:
            if isinstance(c, dict):
                ranked.append(c.get("link") or c.get("url") or "")
            else:
                ranked.append(str(c))
    for u in ranked[:k]:
        if any(g in u or u in g for g in gold):
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
            urls.append(c.get("link") or c.get("url") or "")
    if not urls:
        return 0.0
    unique = len(set(urls))
    return 1.0 - (unique / len(urls))


def candidate_count_delta(before: int, after: int) -> int:
    return before - after


